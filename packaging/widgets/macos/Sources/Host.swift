import AppKit

/// The companion owns only an instruction window; closing it never stops a producer service.
final class HostDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {true}
}

/// A genuine containing application for WidgetKit registration; no simulated desktop panel.
@main enum Host {
    static func main() {
        let app = NSApplication.shared
        let delegate=HostDelegate();app.delegate=delegate
        app.setActivationPolicy(.regular)
        let menu=NSMenu();let appItem=NSMenuItem();let appMenu=NSMenu()
        appMenu.addItem(withTitle:"Quit AG Requests Widgets",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        appItem.submenu=appMenu;menu.addItem(appItem);app.mainMenu=menu
        let window = NSWindow(contentRect: NSRect(x:0,y:0,width:520,height:220), styleMask:[.titled,.closable], backing:.buffered, defer:false)
        window.title = "AG Requests Widgets"
        let label = NSTextField(wrappingLabelWithString: "Add a service's AG requests widget from macOS Edit Widgets. Basic widgets use preferred ports; the service must already be running. WidgetKit controls refresh. No agents or results are simulated. This community build is ad-hoc signed and not notarized.")
        label.frame = NSRect(x:24,y:35,width:470,height:150)
        window.contentView?.addSubview(label)
        window.center();window.makeKeyAndOrderFront(nil);app.activate(ignoringOtherApps:true)
        withExtendedLifetime(delegate) {app.run()}
    }
}
