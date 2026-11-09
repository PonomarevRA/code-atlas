import Foundation
import SafariServices
import CoreFoundation
import LocalAuthentication

/// Receive messages only from this containing Safari extension; never log captured content.
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        guard let item = context.inputItems.first as? NSExtensionItem,
              let message = item.userInfo?[SFExtensionMessageKey] as? [String: Any] else {
            reply(context, ["error": ["code": "native-request-rejected"]]); return
        }
        if message["operation"] as? String == "privacy-configure", let mode = message["mode"] as? String, ["credentials-only", "off"].contains(mode) {
            HumanPrivacyAuthorization(context: context, message: message).start()
        } else {
            do { try AtlasBrowserRequest(context: context, message: message).start() }
            catch { reply(context, ["error": ["code": "native-request-rejected"]]) }
        }
    }
}

/// Return only literal JSON metadata; page titles, DOM and script bytes never enter native replies.
private func reply(_ context: NSExtensionContext, _ value: [String: Any]) {
    let item = NSExtensionItem()
    item.userInfo = [SFExtensionMessageKey: value]
    context.completeRequest(returningItems: [item], completionHandler: nil)
}

private enum BridgeFailure: Error { case invalid }

/// A bounded ephemeral loopback request owns its delegate/session until one terminal response.
// URLSession delegate callbacks use the private serial queue; mutable state is never shared with UI.
final class AtlasBrowserRequest: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let context: NSExtensionContext
    private let message: [String: Any]
    private var session: URLSession?
    private var bytes = Data()
    private var finished = false
    private var status = 0
    private var operation = "capture"
    private var requestedID: String?

    init(context: NSExtensionContext, message: [String: Any]) {
        self.context = context; self.message = message
    }

    /// Accept an explicit operation, never a URL/path/port supplied by extension or page data.
    func start() throws {
        if message["operation"] != nil && !(message["operation"] is String) { throw BridgeFailure.invalid }
        operation = message["operation"] as? String ?? "capture"
        let keys = Set(message.keys)
        var endpoint = "/api/browser-sessions", method = "POST"
        var body: [String: Any]?
        if operation == "capture" {
            guard keys.isSubset(of: ["operation", "capture", "retention", "ttlSeconds"]), message["capture"] is [String: Any] else { throw BridgeFailure.invalid }
            body = message.filter { $0.key != "operation" }
        } else if operation == "privacy-status" || operation == "privacy-configure" {
            endpoint = "/api/browser-privacy"; method = "GET"
            if operation == "privacy-status" {
                guard keys == ["operation"] else { throw BridgeFailure.invalid }
            } else {
                guard keys == ["operation", "mode", "confirmed"], let mode = message["mode"] as? String,
                      ["strict", "credentials-only", "off"].contains(mode), let confirmed = message["confirmed"] as? NSNumber,
                      CFGetTypeID(confirmed) == CFBooleanGetTypeID(), confirmed.boolValue else { throw BridgeFailure.invalid }
                body = ["mode": mode, "confirmed": true]; method = "POST"
            }
        } else if operation == "health" {
            guard keys == ["operation"] else { throw BridgeFailure.invalid }
            method = "GET"
        } else if operation == "status" || operation == "finish" {
            guard keys == ["operation", "sessionId"], let id = message["sessionId"] as? String, validID(id) else { throw BridgeFailure.invalid }
            requestedID = id; endpoint += "/" + id; method = "GET"
            if operation == "finish" { endpoint += "/finish"; method = "POST"; body = ["reason": "completed"] }
        } else { throw BridgeFailure.invalid }
        let encoded = try body.map { try JSONSerialization.data(withJSONObject: $0) }
        guard (encoded?.count ?? 0) <= 8 * 1024 * 1024 else { throw BridgeFailure.invalid }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8765" + endpoint)!)
        request.httpMethod = method; request.httpBody = encoded
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if operation == "privacy-configure" {
            let file = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Atlas Browser/native-control.key")
            let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
            guard attributes[.type] as? FileAttributeType == .typeRegular,
                  let permissions = attributes[.posixPermissions] as? NSNumber, permissions.intValue & 0o077 == 0 else { throw BridgeFailure.invalid }
            let handle = try FileHandle(forReadingFrom: file)
            defer { try? handle.close() }
            let token = try handle.read(upToCount: 33) ?? Data()
            guard token.count == 32 else { throw BridgeFailure.invalid }
            request.setValue(token.map { String(format: "%02x", $0) }.joined(), forHTTPHeaderField: "X-Atlas-Native-Control")
        }
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCache = nil
        config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 20; config.timeoutIntervalForResource = 20
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
        self.session = session; session.dataTask(with: request).resume()
    }

    /// Reject all redirects, including another loopback origin; captured data never follows a Location.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }

    /// Stop collection before a response can exceed its independent 64KB ceiling.
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !finished else { return }
        if bytes.count + data.count > 64_000 { complete(error("native-reply-limit", false, "check-atlas")); dataTask.cancel(); return }
        bytes.append(data)
    }

    /// Capture status without accepting unexpected content length or a redirected origin.
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse, http.url?.host == "127.0.0.1", http.url?.port == 8765, response.expectedContentLength <= 64_000 else {
            completionHandler(.cancel); complete(error("native-reply-limit", false, "check-atlas")); return
        }
        status = http.statusCode; completionHandler(.allow)
    }

    /// Project allowlisted metadata and fixed errors; remote response text is never reflected.
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError failure: Error?) {
        guard !finished else { return }
        if failure != nil { complete(error("atlas-unavailable", true, "start-atlas")); return }
        guard status == 200 else {
            let codes: [Int: (String, Bool, String)] = [404:("browser-item-unavailable",false,"capture-again"),410:("browser-item-unavailable",false,"capture-again"),429:("browser-capacity",true,"finish-owned-session"),503:("browser-busy",true,"retry-later"),422:("invalid-capture",false,"capture-again"),413:("capture-too-large",false,"use-smaller-page"),424:("browser-privacy-unavailable",false,"check-privacy-settings"),403:("native-control-required",false,"check-privacy-settings")]
            let item = codes[status] ?? ("atlas-request-rejected", false, "check-atlas")
            complete(error(item.0, item.1, item.2)); return
        }
        guard let value = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else { complete(error("invalid-atlas-reply",false,"check-atlas")); return }
        if operation == "privacy-status" || operation == "privacy-configure" {
            guard let mode = value["mode"] as? String, ["strict", "credentials-only", "off", "blocked"].contains(mode), let available = value["available"] as? Bool else { complete(error("invalid-atlas-reply", false, "check-atlas")); return }
            complete(["mode": mode, "available": available]); return
        }
        if operation == "health" {
            guard value["sessions"] is [Any] else { complete(error("invalid-atlas-reply",false,"check-atlas")); return }
            complete(["available": true, "protocol": "atlas-browser-agent-v1"]); return
        }
        guard let id = value["sessionId"] as? String, validID(id), requestedID == nil || id == requestedID else { complete(error("invalid-atlas-reply",false,"check-atlas")); return }
        if operation == "finish" {
            guard value["deleted"] as? Bool == true else { complete(error("invalid-atlas-reply",false,"check-atlas")); return }
            complete(["sessionId": id, "deleted": true]); return
        }
        guard let retention = value["retention"] as? String, ["task","hour"].contains(retention),
              let expires = number(value["expiresAt"]), let nodes = number(value["nodeCount"]), let edges = number(value["edgeCount"]),
              nodes >= 0, edges >= 0, nodes <= 1_000_000, edges <= 1_000_000, nodes.rounded() == nodes, edges.rounded() == edges else { complete(error("invalid-atlas-reply",false,"check-atlas")); return }
        var metadata: [String: Any] = ["sessionId":id,"retention":retention,"expiresAt":expires,"nodeCount":Int(nodes),"edgeCount":Int(edges)]
        if let privacy = value["privacy"] as? [String: Any], let mode = privacy["mode"] as? String, ["strict", "credentials-only", "off"].contains(mode) {
            metadata["privacy"] = ["mode": mode, "masked": privacy["masked"] as? Bool == true, "mustDiscloseMasking": privacy["mustDiscloseMasking"] as? Bool == true]
        }
        complete(metadata)
    }

    /// Release network resources exactly once after success, refusal, timeout or cancellation.
    private func complete(_ value: [String: Any]) {
        guard !finished else { return }; finished = true
        reply(context, value); session?.invalidateAndCancel(); session = nil; bytes.removeAll()
    }
}

/// A session identity never becomes an arbitrary path segment.
private func validID(_ value: String) -> Bool { value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) } }

/// JSON booleans cannot masquerade as numeric counters; all accepted values are finite.
private func number(_ value: Any?) -> Double? {
    guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite else { return nil }
    return number.doubleValue
}

/// Recovery fields use a closed vocabulary, independent of remote errors and page content.
private func error(_ code: String, _ retryable: Bool, _ action: String) -> [String: Any] { ["error": ["code":code,"retryable":retryable,"recoveryAction":action]] }

/// User-presence authentication is separate from browser clicks; callback and deadline race only through a lock.
private final class HumanPrivacyAuthorization: @unchecked Sendable {
    private let context: NSExtensionContext
    private let message: [String: Any]
    private let authentication = LAContext()
    private let lock = NSLock()
    private var finished = false
    private var deadline: DispatchWorkItem?

    init(context: NSExtensionContext, message: [String: Any]) { self.context = context; self.message = message }

    func start() {
        authentication.touchIDAuthenticationAllowableReuseDuration = 0
        var failure: NSError?
        guard authentication.canEvaluatePolicy(.deviceOwnerAuthentication, error: &failure) else { complete(false); return }
        let timeout = DispatchWorkItem { self.complete(false) }
        deadline = timeout
        DispatchQueue.global().asyncAfter(deadline: .now() + 20, execute: timeout)
        authentication.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Разрешить передачу оригинальных данных агентам при ослаблении защиты") { success, _ in self.complete(success) }
    }

    /// Exactly one authentication outcome can start the bounded request; cancel/timeout never sends a control credential.
    private func complete(_ success: Bool) {
        lock.lock()
        guard !finished else { lock.unlock(); return }
        finished = true
        lock.unlock()
        deadline?.cancel(); authentication.invalidate()
        guard success else { reply(context, ["error": ["code": "human-confirmation-required"]]); return }
        do { try AtlasBrowserRequest(context: context, message: message).start() }
        catch { reply(context, ["error": ["code": "native-request-rejected"]]) }
    }
}
