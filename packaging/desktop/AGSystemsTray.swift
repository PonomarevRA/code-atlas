import Cocoa

/// Sandbox-compatible counterpart of desktop_support.cocoa_tray; messages have no userInfo.
final class AGSystemsTray: NSObject, NSMenuDelegate {
    private struct Peer {
        let appID: String
        let pid: pid_t
        let ready: Bool
        let seen: TimeInterval
    }
    private let catalogue: [(String, String, String)] = [
        ("code-atlas", "local.code-atlas.desktop", "Code Atlas"),
        ("ag-design", "local.ag-design.desktop", "Ag Design"),
        ("ag-observer", "local.ag-observer.desktop", "Ag Observer"),
        ("ag-tasks", "local.ag-tasks.desktop", "Ag Tasks"),
        ("agmemory", "com.ponomarev.agmemory", "AgMemory"),
        ("atlas-browser", "com.code-atlas.browser", "Atlas Browser")
    ]
    private let namespace = "com.ag-systems.tray.v1"
    private let appID: String
    private let actionHandlers: [String: () -> Void]
    private let pid = ProcessInfo.processInfo.processIdentifier
    private let window: NSWindow
    private let center = DistributedNotificationCenter.default()
    private var peers: [pid_t: Peer] = [:]
    private let activityLabels = ["busy": "Запрос обрабатывается", "success": "Запрос завершён недавно", "error": "Ошибка запроса", "stale": "Состояние устарело", "idle": "Нет активных запросов", "unknown": "Нет телеметрии запросов"]
    private let activityPriority = ["busy", "error", "stale", "success", "idle", "unknown"]
    private let activityFetch = AGTrayActivityFetch()
    private var activityStates: [pid_t: (String, String, TimeInterval)] = [:]
    private var activityStart: TimeInterval = 0
    private var activityReceived = 0
    private var item: NSStatusItem?
    private var timer: Timer?
    private var callbacks: [(String, String)] = []
    private var closed = false
    private var receiveStart: TimeInterval = 0
    private var received = 0
    private var commandStart: TimeInterval = 0
    private var commandsReceived = 0
    private var activeActions = Set<String>()
    private(set) var ready = false

    /// Retain native observers and timer on the main run loop, without changing sandbox entitlements.
    init(window: NSWindow, appID: String = "atlas-browser", actions: [String: () -> Void] = [:]) {
        self.window = window
        self.appID = appID
        self.actionHandlers = actions
        super.init()
        center.addObserver(self, selector: #selector(presence(_:)), name: Notification.Name(namespace + ".presence"), object: nil)
        center.addObserver(self, selector: #selector(activity(_:)), name: Notification.Name(namespace + ".activity"), object: nil)
        for action in ["show", "hide", "quit", "activity", "storage", "updates"] {
            center.addObserver(self, selector: #selector(command(_:)), name: Notification.Name(namespace + "." + appID + "." + action), object: nil)
        }
        NotificationCenter.default.addObserver(self, selector: #selector(applicationHidden), name: NSApplication.didHideNotification, object: NSApp)
        if let button = window.standardWindowButton(.miniaturizeButton) {
            button.isEnabled = true
            button.target = self
            button.action = #selector(hideFromButton(_:))
            button.toolTip = "Скрыть в ag-systems"
        }
        tick()
        let timer = Timer(timeInterval: 2, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    /// Validate the live bundle identity before retaining a small presence record.
    private func verify(_ appID: String, _ pid: pid_t) -> Bool {
        guard let bundle = catalogue.first(where: { $0.0 == appID })?.1,
              let application = NSRunningApplication(processIdentifier: pid) else { return false }
        return !application.isTerminated && application.bundleIdentifier == bundle
    }

    /// Ignore malformed, oversized, self, stale or unknown peers; no notification executes arbitrary data.
    @objc private func presence(_ notification: Notification) {
        let now = ProcessInfo.processInfo.systemUptime
        if now - receiveStart >= 2 { receiveStart = now; received = 0 }
        guard received < 32 else { return }
        received += 1
        guard let value = notification.object as? String, value.utf8.count <= 160 else { return }
        let fields = value.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard fields.count == 4, fields[0] == "1", fields[2].utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }),
              fields[2].count <= 10, let peerPID = Int32(fields[2]), peerPID > 0, peerPID != pid,
              ["0", "1"].contains(fields[3]), verify(fields[1], peerPID) else { return }
        for (pid, peer) in peers where now - peer.seen >= 7 { peers.removeValue(forKey: pid) }
        guard peers[peerPID] != nil || peers.count < 32 else { return }
        peers[peerPID] = Peer(appID: fields[1], pid: peerPID, ready: fields[3] == "1", seen: now)
        // Reconcile once per timer rather than amplifying incoming messages on the UI thread.
    }

    /// Remove expired and reused PIDs before electing or exposing menu commands.
    private func prune() {
        let now = ProcessInfo.processInfo.systemUptime
        for (pid, peer) in peers {
            if now - peer.seen >= 7 || !verify(peer.appID, pid) { peers.removeValue(forKey: pid) }
        }
    }

    /// Publish readiness only after the native item exists; no private/user payload is transmitted.
    @objc private func tick() {
        guard !closed else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let state = activityFetch.state()
        activityStates[pid] = (appID, state, now)
        reconcile()
        center.postNotificationName(Notification.Name(namespace + ".activity"), object: "1|\(appID)|\(pid)|\(state)", userInfo: nil, deliverImmediately: true)
        let value = "1|\(appID)|\(pid)|\(item == nil ? 0 : 1)"
        center.postNotificationName(Notification.Name(namespace + ".presence"), object: value, userInfo: nil, deliverImmediately: true)
    }

    /// A follower removes its fallback icon only after the preferred live owner reports actual readiness.
    private func reconcile() {
        guard !closed else { return }
        prune()
        let leader = ([pid] + peers.keys).min() ?? pid
        let follower = leader != pid && peers[leader]?.ready == true
        if follower, let current = item { NSStatusBar.system.removeStatusItem(current); item = nil }
        if !follower && item == nil {
            let current = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            current.button?.title = "AG"
            current.button?.setAccessibilityLabel("ag-systems")
            let menu = NSMenu(title: "ag-systems")
            menu.delegate = self
            current.menu = menu
            item = current
        }
        ready = item != nil || follower
        updateIndicator()
    }

    /// Rebuild labelled native service submenus only when opened, bounded by the fixed catalogue.
    func menuNeedsUpdate(_ menu: NSMenu) {
        prune()
        menu.removeAllItems()
        callbacks.removeAll()
        let present = Set([appID] + peers.values.map(\.appID))
        for (id, _, title) in catalogue where present.contains(id) {
            let state = stateFor(id)
            let parent = NSMenuItem(title: title + " — " + activityLabels[state]!, action: nil, keyEquivalent: "")
            parent.image = dot(state)
            let submenu = NSMenu(title: title)
            parent.submenu = submenu
            menu.addItem(parent)
            for (label, action) in [("Показать", "show"), ("Скрыть в ag-systems", "hide"), ("Завершить " + title, "quit")] {
                let button = NSMenuItem(title: label, action: #selector(invoke(_:)), keyEquivalent: "")
                button.target = self
                button.tag = callbacks.count
                callbacks.append((id, action))
                submenu.addItem(button)
            }
            if id != "atlas-browser" {
                submenu.addItem(.separator())
                for (label, action) in [("Запросы агентов", "activity"), ("Расположение данных", "storage"), ("Проверить обновления…", "updates")] {
                    let button = NSMenuItem(title: label, action: #selector(invoke(_:)), keyEquivalent: "")
                    button.target = self
                    button.tag = callbacks.count
                    callbacks.append((id, action))
                    submenu.addItem(button)
                }
            }
        }
        menu.addItem(.separator())
        for state in activityPriority {
            let row = NSMenuItem(title: activityLabels[state]!, action: nil, keyEquivalent: "")
            row.image = dot(state)
            menu.addItem(row)
        }
        menu.addItem(NSMenuItem(title: "HTTP-запросы; клиент не подтверждён", action: nil, keyEquivalent: ""))
    }

    /// Retain only fixed-state signals of known live peers, within the presence table's32-peer cap.
    @objc private func activity(_ notification: Notification) {
        let now = ProcessInfo.processInfo.systemUptime
        if now - activityStart >= 2 { activityStart = now; activityReceived = 0 }
        guard !closed, activityReceived < 32 else { return }
        activityReceived += 1
        guard let value = notification.object as? String, value.utf8.count <= 160 else { return }
        let fields = value.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard fields.count == 4, fields[0] == "1", activityLabels[fields[3]] != nil,
              !fields[2].isEmpty, fields[2].count <= 10, fields[2].utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }),
              let peerPID = Int32(fields[2]), let peer = peers[peerPID], peer.appID == fields[1],
              verify(fields[1], peerPID) else { return }
        activityStates[peerPID] = (fields[1], fields[3], now)
    }

    /// Heartbeat freshness and OS membership take precedence over a cached success or busy state.
    private func stateFor(_ id: String) -> String {
        let now = ProcessInfo.processInfo.systemUptime
        var states = Set<String>()
        for (peerPID, (product, state, received)) in activityStates {
            guard peerPID == pid || peers[peerPID] != nil else {
                activityStates.removeValue(forKey: peerPID)
                continue
            }
            if product == id { states.insert(now - received < 7 ? state : "stale") }
        }
        return activityPriority.first(where: { states.contains($0) }) ?? "unknown"
    }

    /// Use adaptive AppKit semantic colours; an adjacent text label carries the same meaning.
    private func dot(_ state: String) -> NSImage {
        let color: NSColor
        switch state {
        case "busy": color = .systemBlue
        case "success": color = .systemGreen
        case "error": color = .systemRed
        case "stale": color = .systemOrange
        default: color = .secondaryLabelColor
        }
        return NSImage(size: NSSize(width: 10, height: 10), flipped: false) { _ in
            color.setFill()
            NSBezierPath(ovalIn: NSRect(x: 1, y: 1, width: 8, height: 8)).fill()
            return true
        }
    }

    /// Update only cached native presentation, including an open menu without rebuilding callbacks.
    private func updateIndicator() {
        // Followers must prune departed PIDs even when their own menu item is absent.
        for peerPID in activityStates.keys where peerPID != pid && peers[peerPID] == nil {
            activityStates.removeValue(forKey: peerPID)
        }
        guard let item else { return }
        let present = Set([appID] + peers.values.map(\.appID))
        let products = catalogue.filter { present.contains($0.0) }
        let states = products.map { stateFor($0.0) }
        let overall = activityPriority.first(where: { states.contains($0) }) ?? "unknown"
        let active = products.filter { stateFor($0.0) == "busy" }.map { $0.2 }
        var title = "AG"
        if active.count == 1 { title += " · " + active[0] }
        else if !active.isEmpty { title += " · " + String(active.count) }
        item.button?.title = title
        item.button?.image = dot(overall)
        let detail = products.map { $0.2 + ": " + activityLabels[stateFor($0.0)]! }.joined(separator: "\n")
        item.button?.toolTip = "ag-systems\n" + detail
        item.button?.setAccessibilityLabel("ag-systems\n" + detail)
        for row in item.menu?.items ?? [] {
            guard let submenu = row.submenu,
                  let product = products.first(where: { $0.2 == submenu.title }) else { continue }
            let state = stateFor(product.0)
            row.title = product.2 + " — " + activityLabels[state]!
            row.image = dot(state)
        }
    }

    /// Accept only the actual loopback origin announced by this host's own child.
    func setActivityOrigin(_ origin: URL) { activityFetch.configure(origin: origin, appID: appID) }

    /// Forward only a fixed native menu selector to the addressed known app.
    @objc private func invoke(_ sender: NSMenuItem) {
        guard callbacks.indices.contains(sender.tag) else { return }
        let (id, action) = callbacks[sender.tag]
        if id == appID { perform(action) }
        else { center.postNotificationName(Notification.Name(namespace + "." + id + "." + action), object: nil, userInfo: nil, deliverImmediately: true) }
    }

    /// Distributed messages carry no values and can only select the fixed native UI vocabulary.
    @objc private func command(_ notification: Notification) {
        let now = ProcessInfo.processInfo.systemUptime
        if now - commandStart >= 1 { commandStart = now; commandsReceived = 0 }
        guard commandsReceived < 8 else { return }
        commandsReceived += 1
        guard let action = notification.name.rawValue.split(separator: ".").last else { return }
        perform(String(action))
    }

    /// Actual Quit terminates this lightweight host; Safari's extension lifecycle remains Safari-owned.
    private func perform(_ action: String) {
        guard !closed else { return }
        if let callback = actionHandlers[action] {
            // Modal AppKit callbacks run a nested loop; repeated messages must not stack dialogs.
            guard NSApp.modalWindow == nil || action == "show" else { return }
            guard activeActions.insert(action).inserted else { return }
            defer { activeActions.remove(action) }
            callback()
            return
        }
        switch action {
        case "show": show()
        case "hide": hide()
        case "quit": NSApp.terminate(nil)
        default: break
        }
    }

    /// The standard yellow control uses the same guarded background policy as Close.
    @objc private func hideFromButton(_ sender: Any?) { hide() }

    /// Cmd+H also removes Dock presence after an actual restore item is available.
    @objc private func applicationHidden(_ notification: Notification) {
        if ready { NSApp.setActivationPolicy(.accessory) }
    }

    /// Hide the application like Cmd+H while keeping a usable restore path.
    func hide() {
        reconcile()
        guard ready else { return }
        NSApp.hide(nil)
        NSApp.setActivationPolicy(.accessory)
    }

    /// Restore this same window and its normal Dock/menu identity.
    func show() {
        NSApp.setActivationPolicy(.regular)
        window.deminiaturize(nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.unhide(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Explicit teardown releases native resources without blocking or creating another run loop.
    func close() {
        guard !closed else { return }
        closed = true
        ready = false
        timer?.invalidate()
        activityFetch.close()
        timer = nil
        center.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
        if let current = item { NSStatusBar.system.removeStatusItem(current) }
        item = nil
    }
}


/// A bounded URLSession delegate keeps widget metadata off AppKit and out of the model/context.
private final class AGTrayActivityFetch: NSObject, URLSessionDataDelegate {
    private let lock = NSLock()
    private var session: URLSession?
    private var timer: DispatchSourceTimer?
    private var task: URLSessionDataTask?
    private var data = Data()
    private var product = ""
    private var latest = "unknown"
    private var received = ProcessInfo.processInfo.systemUptime
    private var closed = false

    /// Configure once, rejecting redirects, credentials, external origins and arbitrary path selectors.
    func configure(origin: URL, appID: String) {
        guard origin.scheme == "http", ["127.0.0.1", "[::1]", "::1"].contains(origin.host ?? ""),
              origin.port != nil, origin.user == nil, origin.password == nil,
              ["", "/"].contains(origin.path), origin.query == nil, origin.fragment == nil else { return }
        lock.lock()
        guard session == nil, !closed else { lock.unlock(); return }
        product = appID
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 1.5
        config.timeoutIntervalForResource = 1.5
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
        let source = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        let path = appID == "agmemory" ? "/api/desktop/v1/widget" : "/api/desktop/v1/activity"
        let url = origin.appendingPathComponent(path)
        source.setEventHandler { [weak self] in self?.poll(url) }
        source.schedule(deadline: .now(), repeating: 2)
        timer = source
        lock.unlock()
        source.resume()
    }

    /// The main thread reads a cached enum; an absent fresh response becomes stale after eight seconds.
    func state() -> String {
        lock.lock(); defer { lock.unlock() }
        if session == nil { return "unknown" }
        return ProcessInfo.processInfo.systemUptime - received < 8 ? latest : "stale"
    }

    /// At most one socket request can exist even if a service stalls or a timer fires repeatedly.
    private func poll(_ url: URL) {
        lock.lock(); defer { lock.unlock() }
        guard !closed, task == nil, let session else { return }
        data.removeAll(keepingCapacity: false)
        let next = session.dataTask(with: url)
        task = next
        next.resume()
    }

    /// Refuse every redirect before a request can leave the exact owned loopback URL.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    /// Refuse wrong status and oversized declared bodies before allocating their contents.
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        let valid = (response as? HTTPURLResponse)?.statusCode == 200 && response.expectedContentLength <= 65536
        completionHandler(valid ? .allow : .cancel)
    }

    /// Enforce the same64KiB cap for chunked/incorrect Content-Length responses.
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive part: Data) {
        lock.lock()
        let valid = data.count + part.count <= 65536 && !closed
        if valid { data.append(part) }
        lock.unlock()
        if !valid { dataTask.cancel() }
    }

    /// Discard every field except fixed identity, timestamps and outcome enums; never log raw JSON.
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock(); defer { lock.unlock() }
        self.task = nil
        defer { data.removeAll(keepingCapacity: false) }
        guard !closed, error == nil, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              value["appId"] as? String == product else { return }
        let state: String
        if value["schemaVersion"] as? String == "ag-desktop/activity-v1" {
            guard let fixed = value["state"] as? String,
                  ["busy", "success", "error", "stale", "idle", "unknown"].contains(fixed) else { return }
            state = fixed
        } else if product == "agmemory", value["schemaVersion"] as? String == "ag-desktop/widget-v1" {
            state = Self.widgetState(value)
        } else { return }
        latest = state
        received = ProcessInfo.processInfo.systemUptime
    }

    /// A bounded journal snapshot proves recent outcomes; a started event alone never proves a still-running request.
    private static func widgetState(_ value: [String: Any]) -> String {
        guard value["status"] as? String == "ok", let rows = value["requests"] as? [[String: Any]], rows.count <= 8 else { return "unknown" }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        func date(_ raw: Any?) -> Date? {
            guard let raw = raw as? String, raw.count <= 64 else { return nil }
            if let date = formatter.date(from: raw) { return date }
            let simple = ISO8601DateFormatter()
            return simple.date(from: raw)
        }
        guard let captured = date(value["capturedAt"]), (-2...8).contains(Date().timeIntervalSince(captured)) else { return "stale" }
        var latest: (Date, String)?
        for row in rows {
            guard let timestamp = date(row["timestamp"]), let outcome = row["status"] as? String,
                  latest == nil || timestamp > latest!.0 else { continue }
            latest = (timestamp, outcome)
        }
        guard let (timestamp, outcome) = latest, (-2..<15).contains(Date().timeIntervalSince(timestamp)) else { return "idle" }
        switch outcome {
        case "succeeded", "success", "ok": return "success"
        case "failed", "error", "cancelled": return "error"
        // Widget history is bounded; a missing finish event is not an active-request proof.
        default: return "unknown"
        }
    }

    /// Cancel native resources without waiting for socket callbacks on the main thread.
    func close() {
        lock.lock()
        closed = true
        timer?.cancel(); timer = nil
        let current = session
        session = nil
        lock.unlock()
        current?.invalidateAndCancel()
    }
}
