"""Exercise the real macOS status item without opening production stores or quitting the OS."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    """Create, hide, restore and dispose one test tray on AppKit's main thread."""
    import AppKit
    from desktop_support.tray_platforms import CocoaTray
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    invoked = []
    handle = SimpleNamespace(title='AG tray validation', ready=False, closed=False,
        show=lambda: invoked.append('show'), activity=lambda: invoked.append('activity'),
        storage=lambda: None, updates=lambda: None, on_quit=lambda: invoked.append('quit'))
    CocoaTray(handle)
    assert handle.ready and handle.native.item.button().toolTip() == handle.title
    assert handle.native.item.menu().numberOfItems() == 5
    # The callbacks are retained separately because AppKit holds weak menu targets.
    handle.native.target.callbacks[0]()
    handle.native.target.callbacks[-1]()
    assert invoked == ['show', 'quit']
    handle.native.close()
    print('PASS real NSStatusItem registration, retained Open/Quit callbacks, native disposal')


if __name__ == '__main__':
    main()
