import Foundation

/// Fixed identities and preferred ports; configuration can change only the numeric loopback port.
enum AGApplication: String, CaseIterable, Codable {
    case atlas = "code-atlas", memory = "agmemory", observer = "ag-observer", tasks = "ag-tasks", design = "ag-design"
    var port: Int { switch self { case .atlas: 8765; case .memory: 8891; case .observer: 8890; case .tasks: 8895; case .design: 8897 } }
}

struct Operation: Codable, Identifiable {
    let timestamp, operation, actorLabel, status, requestSummary, responseSummary: String
    let durationMs: Double?
    var id: String { timestamp + operation }
}

struct Snapshot: Codable {
    let schemaVersion, appId, applicationVersion, capturedAt, status, origin, source: String
    let requests: [Operation]
    var captureDate: Date? {
        let format = ISO8601DateFormatter(); format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return format.date(from: capturedAt) ?? ISO8601DateFormatter().date(from: capturedAt)
    }
    /// Reject foreign identity, malformed metadata, oversized rows and snapshots from another port.
    func validate(application: AGApplication, origin expected: String) throws {
        guard schemaVersion == "ag-desktop/widget-v1", appId == application.rawValue, origin == expected,
              ["ok", "degraded", "unavailable"].contains(status), requests.count <= 8, captureDate != nil,
              [applicationVersion, capturedAt, source].allSatisfy({ $0.count <= 512 }) else { throw FetchError.invalidSnapshot }
        for row in requests {
            guard [row.timestamp,row.operation,row.actorLabel,row.status,row.requestSummary,row.responseSummary].allSatisfy({$0.count <= 512}),
                  row.durationMs == nil || (row.durationMs!.isFinite && row.durationMs! >= 0) else { throw FetchError.invalidSnapshot }
        }
    }
}

enum FetchError: Error { case unavailable, oversized, invalidSnapshot }

/// A single bounded, nonredirecting request; proxies and cookies are disabled even for loopback.
final class BoundedFetch: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private var data = Data()
    private var continuation: CheckedContinuation<Data, Error>?
    private var session: URLSession?
    private var failure: Error?
    static let maximumBytes = 65_536

    func fetch(_ url: URL) async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            let configuration = URLSessionConfiguration.ephemeral
            configuration.connectionProxyDictionary = [:]
            configuration.httpCookieStorage = nil
            configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
            configuration.timeoutIntervalForRequest = 3
            configuration.timeoutIntervalForResource = 4
            session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
            session!.dataTask(with: url).resume()
        }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        failure = FetchError.unavailable; completionHandler(nil)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              response.expectedContentLength <= Int64(Self.maximumBytes) else {
            failure = FetchError.unavailable; completionHandler(.cancel); return
        }
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) {
        guard data.count + chunk.count <= Self.maximumBytes else { failure = FetchError.oversized; dataTask.cancel(); return }
        data.append(chunk)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let complete = continuation; continuation = nil
        if let error = failure ?? error { complete?.resume(throwing: error) } else { complete?.resume(returning: data) }
        session.finishTasksAndInvalidate(); self.session = nil
    }
}

/// The caller chooses an allowlisted application and optionally its actual numeric port, never a URL.
func fetchSnapshot(application: AGApplication, port: Int?) async throws -> Snapshot {
    let selected = port ?? application.port
    guard (1...65535).contains(selected) else { throw FetchError.invalidSnapshot }
    let origin = "http://127.0.0.1:\(selected)"
    let data = try await BoundedFetch().fetch(URL(string: origin + "/api/desktop/v1/widget")!)
    let snapshot = try JSONDecoder().decode(Snapshot.self, from: data)
    try snapshot.validate(application: application, origin: origin)
    return snapshot
}
