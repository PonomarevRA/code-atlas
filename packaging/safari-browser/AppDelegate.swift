import Cocoa

/// The containing app keeps only its settings window; Safari owns the actual extension process.
@main
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var tray: AGSystemsTray?
    private var window: NSWindow?

    /// Attach after the storyboard creates the window; no browser or service is started here.
    func applicationDidFinishLaunching(_ notification: Notification) {
        attachWindow(NSApp.windows.first { $0.styleMask.contains(.titled) })
    }

    /// Storyboard windows may appear after launch; the view controller supplies the actual window once.
    func attachWindow(_ candidate: NSWindow?) {
        guard tray == nil, let window = candidate else { return }
        self.window = window
        window.isReleasedWhenClosed = false
        window.delegate = self
        tray = AGSystemsTray(window: window)
        if CommandLine.arguments.contains("--background") {
            // Wait until storyboard presentation finishes so it cannot unhide the app again.
            DispatchQueue.main.async { [weak self] in self?.tray?.hide() }
        }
        window.styleMask.insert(.miniaturizable)
        if let button = window.standardWindowButton(.miniaturizeButton) {
            button.isEnabled = true
            button.target = self
            button.action = #selector(hideWindow(_:))
            button.toolTip = "Скрыть в ag-systems"
        }
    }

    /// Closing is reversible only when a real menu can restore this exact window.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard tray?.ready == true else { return true }
        tray?.hide()
        return false
    }

    /// The real yellow traffic light hides instead of creating a Dock thumbnail.
    @objc func hideWindow(_ sender: Any?) { tray?.hide() }

    /// Cmd+H also leaves only the shared status item, after readiness is established.
    func applicationDidHide(_ notification: Notification) {
        if tray?.ready == true { NSApp.setActivationPolicy(.accessory) }
    }

    /// Reopening restores the existing window rather than another copy or service.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        tray?.show()
        return true
    }

    /// A missing native tray retains the standard visible app/quit fallback.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return tray?.ready != true
    }

    /// Explicit Cmd+Q/OS shutdown remains ordinary termination, never a close veto.
    func applicationWillTerminate(_ notification: Notification) { tray?.close() }
}

