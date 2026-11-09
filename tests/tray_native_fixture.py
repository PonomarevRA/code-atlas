"""Disposable AppKit process: private notification namespace, no browser/window activation."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit
from Foundation import NSObject, NSTimer
from desktop_support.cocoa_tray import CocoaTray
import desktop_support.cocoa_tray as tray_module
class SyntheticActivity:
    """Use fixed synthetic states, never an installed service endpoint."""
    def __init__(self, app_id, origin):
        self.value = 'busy' if app_id == 'one' else 'success'
    def read(self):
        return self.value
    def close(self):
        pass
tray_module.ActivityPoller = SyntheticActivity

parser = argparse.ArgumentParser()
parser.add_argument('app_id')
parser.add_argument('namespace')
parser.add_argument('report', type=Path)
args = parser.parse_args()
app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((-10000, -10000), (300, 200)), AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable,
    AppKit.NSBackingStoreBuffered, False)
window.setReleasedWhenClosed_(False)
bundle = AppKit.NSRunningApplication.currentApplication().bundleIdentifier()
handle = SimpleNamespace(app_id=args.app_id, window=SimpleNamespace(native=window), ready=False, closed=False,
                         origin='http://127.0.0.1:1', on_quit=lambda: app.stop_(None), activity=lambda: None, storage=lambda: None, updates=lambda: None)
tray = CocoaTray(handle, catalog={'one': (bundle, 'Fixture One'), 'two': (bundle, 'Fixture Two')}, namespace=args.namespace)

class Reporter(NSObject):
    """Write only synthetic presence state; no screenshots/accessibility or real application data."""
    def update_(self, sender):
        """Atomic metadata lets the parent assert steady-state ownership and disposal."""
        value = {'pid': os.getpid(), 'ready': handle.ready, 'ownsIcon': tray.item is not None,
                 'iconTitle': str(tray.item.button().title()) if tray.item is not None else None,
                 'activityEntries': len(tray.activity_states), 'leader': tray.group.leader(__import__('time').monotonic()), 'peers': len(tray.group.peers)}
        temporary = args.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.replace(args.report)

reporter = Reporter.alloc().init()
timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.2, reporter, 'update:', None, True)
try:
    app.run()
finally:
    timer.invalidate()
    tray.close()
