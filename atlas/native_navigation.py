"""Native project and history buttons reuse the owned page's existing navigation."""

import json
import logging
from threading import Thread

_target_class = None

BUTTONS = (
    ('projects', 'Выбрать проект', 'folder'),
    ('back', 'Назад по карте', 'arrow.left'),
    ('forward', 'Вперёд по карте', 'arrow.right'),
)
COMMANDS = {
    'projects': 'document.querySelector(\'.workspace-brand[href="/"]\')?.click()',
    'back': "document.getElementById('map-history-back')?.click()",
    'forward': "document.getElementById('map-history-forward')?.click()",
}

READ_STATE = """JSON.stringify((() => {
 const back=document.getElementById('map-history-back'),forward=document.getElementById('map-history-forward');
 return back&&forward?{back:!back.disabled,forward:!forward.disabled,
 projects:!!document.querySelector('.workspace-brand[href="/"]')}:null;
})())"""
WATCH_STATE = """(() => {
 window.__atlasNativeHistoryCleanup?.();
 const back=document.getElementById('map-history-back'),forward=document.getElementById('map-history-forward');
 if(!back||!forward)return;
 let disposed=false;
 const notify=()=>{if(!disposed)window.webkit.messageHandlers.standaloneSidebar.postMessage(null);};
 const observer=new MutationObserver(notify);
 for(const button of [back,forward])observer.observe(button,{attributes:true,attributeFilter:['disabled']});
 const dispose=()=>{disposed=true;observer.disconnect();window.removeEventListener('pagehide',dispose);};
 window.__atlasNativeHistoryCleanup=dispose;window.addEventListener('pagehide',dispose,{once:true});notify();
})()"""


class MapNavigation:
    """Keep project selection and history controls in sync with the owned map document."""

    sidebar_x = 110
    required_width = 148

    def __init__(self, window, presentation):
        """Retain the origin/lifecycle authority; no URL or script is accepted from the page."""
        self.window, self.presentation = window, presentation
        self.closed = False
        self.buttons = {}

    def install(self, view):
        """Create real SF Symbol buttons on Cocoa's main thread, initially disabled."""
        import AppKit
        self.target = _target_type().alloc().init()
        self.target.navigation = self
        for name, title, symbol in BUTTONS:
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
            if image is None:
                raise RuntimeError('Native map navigation symbol unavailable')
            button = AppKit.NSButton.buttonWithImage_target_action_(image, self.target, name + ':')
            button.setBordered_(False)
            button.setToolTip_(title)
            button.setAccessibilityLabel_(title)
            button.setEnabled_(False)
            view.addSubview_(button)
            self.buttons[name] = button

    def layout(self, height):
        """Reserve separate 28pt targets for projects/history before the sidebar toggle."""
        for index, (name, _, _) in enumerate(BUTTONS):
            self.buttons[name].setFrame_(((2 + index * 36, (height - 28) / 2), (28, 28)))

    def start(self):
        """Observe only disabled-state changes; the existing bounded notifier coalesces updates."""
        def watch():
            """Install a CSP-safe watcher only in the exact owned main document."""
            if not self.closed and self.presentation.active() and self.presentation._same_origin():
                self.window.run_js(WATCH_STATE)
        Thread(target=_run, args=(watch,), name='native-history-watch', daemon=True).start()

    def refresh(self):
        """Pull availability flags on the presentation worker and reject stale document reads."""
        if self.closed or not self.presentation.active() or not self.presentation._same_origin():
            return
        generation = self.presentation.document_generation
        value = self.window.run_js(READ_STATE)
        if isinstance(value, str):
            value = json.loads(value)
        valid = isinstance(value, dict) and set(value) == set(COMMANDS)
        if not valid or any(type(flag) is not bool for flag in value.values()):
            value = dict.fromkeys(COMMANDS, False)
        if not self.presentation._same_origin():
            return
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self.apply, value, generation)

    def apply(self, state, generation):
        """Never enable a button after reload, accessory replacement or native shutdown."""
        accessory = getattr(self.window, '_standalone_sidebar_accessory', None)
        if self.closed or not self.presentation.active() or generation != self.presentation.document_generation:
            return
        if accessory is None or accessory.navigation is not self:
            return
        for name, button in self.buttons.items():
            button.setEnabled_(state[name])
        from .native_toolbar import _set_document_capability
        _set_document_capability(self.window, state['projects'], 'native-project-picker', '_standalone_native_project_picker')

    def activate(self, name):
        """Execute only fixed project/history DOM actions in the exact owned, active document."""
        if name not in COMMANDS or self.closed or not self.presentation.active():
            return
        if not self.presentation._same_origin():
            return
        self.window.run_js(COMMANDS[name])

    def close(self):
        """Make late callbacks inert; pagehide/reinstallation disposes the document's watcher."""
        self.closed = True


def _run(action):
    """Keep action failures visible without raising through Cocoa's responder loop."""
    try:
        action()
    except Exception:
        logging.getLogger(__name__).exception('Native map navigation unavailable')


def _target_type():
    """Register only the three fixed project/history native actions once per process."""
    global _target_class
    if _target_class is None:
        from Foundation import NSObject
        import objc

        class AtlasMapHistoryTarget(NSObject):
            """Dispatch fixed native actions off Cocoa's UI thread."""

            def projects_(self, sender):
                """Open the same project selection page as the browser's CA link."""
                self._dispatch('projects')

            def back_(self, sender):
                """Use the existing map Back action."""
                self._dispatch('back')

            def forward_(self, sender):
                """Use the existing map Forward action."""
                self._dispatch('forward')

            @objc.python_method
            def _dispatch(self, name):
                """Keep pywebview's synchronous JavaScript call out of the main event loop."""
                Thread(target=_run, args=(lambda: self.navigation.activate(name),), name='native-history-click', daemon=True).start()

        _target_class = AtlasMapHistoryTarget
    return _target_class
