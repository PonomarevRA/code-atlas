extension AGSystemsTray {
    /// A thousand untrusted presence callbacks cannot amplify native process lookups.
    func verifyFloodBudget() {
        let before = trayIdentityLookupCount
        for _ in 0..<1000 {
            presence(Notification(name: Notification.Name("test"), object: "1|code-atlas|2147483647|1"))
        }
        precondition(trayIdentityLookupCount - before <= 32)
        precondition(received == 32)
    }

    /// Non-owners bound cached PID churn; an open menu updates without rebuilding targets.
    static func verifyActivityPresentation(window: NSWindow) {
        let probe = AGSystemsTray(window: window)
        let menu = probe.item!.menu!
        probe.menuNeedsUpdate(menu)
        precondition(menu.items.contains(where: { $0.title == "Запрос обрабатывается" }))
        probe.activityStates[probe.pid] = (probe.appID, "busy", ProcessInfo.processInfo.systemUptime)
        probe.updateIndicator()
        precondition(probe.item!.button!.title == "AG · Atlas Browser")
        precondition(menu.items[0].title.contains("Запрос обрабатывается"))
        NSStatusBar.system.removeStatusItem(probe.item!)
        probe.item = nil
        for candidate in 100000..<101000 {
            let pid = pid_t(candidate)
            probe.peers = [pid: Peer(appID: "code-atlas", pid: pid, ready: true, seen: ProcessInfo.processInfo.systemUptime)]
            probe.activityStates[pid] = ("code-atlas", "busy", ProcessInfo.processInfo.systemUptime)
            probe.updateIndicator()
            precondition(probe.activityStates.count <= 2)
        }
        probe.close()
    }

    /// Reentrant callbacks model NSAlert's nested run loop without presenting any dialog.
    static func verifyCallbackCoalescing(window: NSWindow) {
        for action in ["storage", "updates", "activity"] {
            var calls = 0
            var probe: AGSystemsTray!
            probe = AGSystemsTray(window: window, actions: [action: {
                calls += 1
                if calls < 1000 { probe.perform(action) }
            }])
            probe.perform(action)
            precondition(calls == 1)
            probe.close()
        }
    }
}

@main
struct TrayChecks {
    /// Exercise actual AppKit lifecycle with an invisible window and isolated notification namespace.
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = AppDelegate()
        delegate.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification))
        let window = NSWindow(contentRect: NSRect(x: -10000, y: -10000, width: 300, height: 200), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        delegate.attachWindow(window)
        delegate.attachWindow(window) // Repeated view appearance must not install another native owner.
        precondition(window.standardWindowButton(.miniaturizeButton)?.isEnabled == true)
        precondition(delegate.windowShouldClose(window) == false)
        precondition(app.activationPolicy() == .accessory)
        precondition(delegate.applicationShouldTerminateAfterLastWindowClosed(app) == false)
        let flood = AGSystemsTray(window: window)
        flood.verifyFloodBudget()
        flood.close()
        AGSystemsTray.verifyCallbackCoalescing(window: window)
        AGSystemsTray.verifyActivityPresentation(window: window)
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        precondition(delegate.windowShouldClose(window) == true)
        precondition(delegate.applicationShouldTerminateAfterLastWindowClosed(app) == true)
        print("{\"status\":\"PASS\",\"nativeHostCloseKeepsProcess\":true,\"yellowButtonEnabled\":true,\"dockPolicyAccessory\":true,\"explicitQuitDisposesTray\":true,\"thousandPresenceLookupsAtMost32\":true,\"modalCallbackReentryCoalesced\":true,\"windowActivated\":false}")
    }
}
