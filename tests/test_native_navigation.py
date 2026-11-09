"""Native map actions preserve the owned history and reject stale lifecycle work."""

import json
import sys
from types import SimpleNamespace

import pytest

from atlas.native_navigation import MapNavigation


class Button:
    """Retain the effective native enabled state for lifecycle assertions."""

    def __init__(self):
        self.enabled = False

    def setEnabled_(self, value):
        """Model Cocoa's enabled-state setter without starting an event loop."""
        self.enabled = value

    def setFrame_(self, frame):
        """Retain native hit-target bounds for overlap checks."""
        self.frame = frame


def fixture_navigation(monkeypatch, result):
    """Capture main-thread delivery and fixed DOM calls independently of native rendering."""
    queued, scripts = [], []
    helper = SimpleNamespace(callAfter=lambda function, *args: queued.append((function, args)))
    monkeypatch.setitem(sys.modules, 'PyObjCTools', SimpleNamespace(AppHelper=helper))
    monkeypatch.setattr('atlas.native_toolbar._set_document_capability',
                        lambda window, available, name, cache: setattr(window, cache, available))
    binding = SimpleNamespace(document_generation=3, active=lambda: True, _same_origin=lambda: True)
    window = SimpleNamespace(run_js=lambda script: scripts.append(script) or json.dumps(result))
    navigation = MapNavigation(window, binding)
    navigation.buttons = {'projects': Button(), 'back': Button(), 'forward': Button()}
    window._standalone_sidebar_accessory = SimpleNamespace(navigation=navigation)
    return navigation, binding, queued, scripts


def test_native_enabled_state_follows_the_existing_map_history(monkeypatch):
    """A real state change enables Back and leaves Forward unavailable until its DOM state changes."""
    navigation, _, queued, _ = fixture_navigation(monkeypatch, {'back': True, 'forward': False, 'projects': True})
    navigation.refresh()
    assert len(queued) == 1
    function, args = queued.pop()
    function(*args)
    assert navigation.buttons['back'].enabled
    assert not navigation.buttons['forward'].enabled
    assert navigation.buttons['projects'].enabled
    assert navigation.window._standalone_native_project_picker


@pytest.mark.parametrize('change', ['reload', 'close', 'replace'])
def test_stale_native_updates_cannot_reenable_controls(monkeypatch, change):
    """A state read queued before reload, disposal or accessory replacement cannot mutate new controls."""
    navigation, binding, queued, _ = fixture_navigation(monkeypatch, {'back': True, 'forward': True, 'projects': True})
    navigation.refresh()
    if change == 'reload':
        binding.document_generation += 1
    elif change == 'close':
        navigation.close()
    else:
        navigation.window._standalone_sidebar_accessory = SimpleNamespace(navigation=object())
    function, args = queued.pop()
    function(*args)
    assert not any(button.enabled for button in navigation.buttons.values())
    assert not hasattr(navigation.window, '_standalone_native_project_picker')


@pytest.mark.parametrize('result', [None, {'back': 1, 'forward': True, 'projects': True},
                                  {'back': True, 'forward': True},
                                  {'back': True, 'forward': True, 'projects': True, 'command': 'open'}])
def test_incomplete_or_extended_presentation_never_enables_buttons(monkeypatch, result):
    """Incomplete or malformed flags never hide the project chooser fallback."""
    navigation, _, queued, _ = fixture_navigation(monkeypatch, result)
    navigation.refresh()
    function, args = queued.pop()
    function(*args)
    assert not any(button.enabled for button in navigation.buttons.values())
    assert not navigation.window._standalone_native_project_picker


def test_actions_are_fixed_and_foreign_or_closed_documents_are_inert(monkeypatch):
    """Native clicks invoke existing map buttons, never URL history or a page-selected script."""
    navigation, binding, _, scripts = fixture_navigation(monkeypatch, None)
    navigation.activate('back')
    navigation.activate('forward')
    navigation.activate('projects')
    assert scripts == ["document.getElementById('map-history-back')?.click()", "document.getElementById('map-history-forward')?.click()",
                       'document.querySelector(\'.workspace-brand[href="/"]\')?.click()']
    navigation.activate('open-url')
    binding._same_origin = lambda: False
    navigation.activate('back')
    navigation.activate('projects')
    navigation.refresh()
    binding._same_origin = lambda: True
    navigation.close()
    navigation.activate('forward')
    navigation.activate('projects')
    assert len(scripts) == 3


def test_navigation_during_dom_pull_discards_the_result(monkeypatch):
    """A document that leaves the owned origin while the read runs cannot enqueue native updates."""
    navigation, binding, queued, _ = fixture_navigation(monkeypatch, {'back': True, 'forward': True})

    def leave_origin(script):
        """Simulate a WebView navigation racing the synchronous measurement."""
        binding._same_origin = lambda: False
        return {'back': True, 'forward': True}

    navigation.window.run_js = leave_origin
    navigation.refresh()
    assert not queued


def test_missing_project_link_restores_only_its_fallback(monkeypatch):
    """History remains usable when project selection is unavailable in the current document."""
    navigation, _, queued, _ = fixture_navigation(monkeypatch, {'back': True, 'forward': False, 'projects': False})
    navigation.refresh()
    function, args = queued.pop()
    function(*args)
    assert navigation.buttons['back'].enabled
    assert not navigation.buttons['projects'].enabled
    assert not navigation.window._standalone_native_project_picker


def test_project_history_and_sidebar_targets_do_not_overlap(monkeypatch):
    """Expanded and collapsed accessories reserve enough width for all native targets."""
    navigation, _, _, _ = fixture_navigation(monkeypatch, None)
    navigation.layout(38)
    bounds = [navigation.buttons[name].frame for name in ('projects', 'back', 'forward')]
    bounds.append(((navigation.sidebar_x, 5), (28, 28)))
    for left, right in zip(bounds, bounds[1:]):
        assert left[0][0] + left[1][0] < right[0][0]
    assert bounds[-1][0][0] + bounds[-1][1][0] <= navigation.required_width
