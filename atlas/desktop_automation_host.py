"""Bounded automation of the owned desktop WebView on its platform UI thread."""

import base64
import json
import logging
import sys
import time
from threading import Event, Lock
from urllib.parse import urlsplit
from uuid import uuid4

_CAPTURE_LIMIT = 4 * 1024 * 1024
_SCRIPT_LIMIT = 64 * 1024
_TIMEOUT = 22
_LOG = logging.getLogger(__name__)


def _error(code):
    return {"status": "error", "error": code}


def _owned_origin(url):
    """Normalize the configured loopback origin without accepting credentials."""
    parsed = urlsplit(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        return None
    default = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}" + (f":{port}" if port != default else "")


def _capture_result(data, adapter):
    """Expose bounded PNG bytes with the actual renderer that produced them."""
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _error("capture_failed")
    if len(data) > _CAPTURE_LIMIT:
        return _error("capture_too_large")
    return {"status": "ok", "pngBase64": base64.b64encode(data).decode("ascii"), "adapter": adapter}


def _macos_view(window):
    view = window.native.contentView()
    if not hasattr(view, "evaluateJavaScript_completionHandler_"):
        raise NotImplementedError("WKWebView unavailable")
    return view


def _macos_capture(view, done):
    """Ask WKWebView for its own content image on the Cocoa main thread."""
    import AppKit
    import WebKit

    if not hasattr(view, "takeSnapshotWithConfiguration_completionHandler_"):
        done(_error("capture_unsupported"))
        return
    size = view.bounds().size
    if size.width <= 0 or size.height <= 0:
        done(_error("capture_failed"))
        return
    width = min(1280, size.width)
    scale = view.window().backingScaleFactor() if view.window() else 1
    if width * width * size.height / size.width * scale ** 2 > 8_000_000:
        done(_error("capture_too_large"))
        return
    config = WebKit.WKSnapshotConfiguration.alloc().init()
    config.setSnapshotWidth_(width)

    def complete(image, error):
        try:
            if error or image is None:
                done(_error("capture_failed"))
                return
            rep = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
            png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
            done(_capture_result(bytes(png), "wkwebview"))
        except Exception:
            _LOG.debug("WKWebView snapshot conversion failed", exc_info=True)
            done(_error("capture_failed"))

    view.takeSnapshotWithConfiguration_completionHandler_(config, complete)


def _windows_task(task, done, failure):
    """Observe a .NET task without waiting synchronously on the GUI thread."""
    from System import Action

    def complete():
        try:
            task.GetAwaiter().GetResult()
            done()
        except Exception:
            _LOG.debug("WebView2 task failed", exc_info=True)
            failure()

    task.GetAwaiter().OnCompleted(Action(complete))


def _windows_script(view, script, done):
    task = view.ExecuteScriptAsync(script)
    _windows_task(task, lambda: done(json.loads(str(task.Result))), lambda: done(None))


def _windows_capture(view, done):
    """Encode the current WebView2 surface to a private in-memory PNG stream."""
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    from System.IO import MemoryStream

    if view.Width * view.Height > 8_000_000:
        done(_error("capture_too_large"))
        return
    stream = MemoryStream()
    try:
        task = view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream)
    except Exception:
        stream.Dispose()
        raise

    def complete(ok):
        try:
            if not ok:
                done(_error("capture_failed"))
            elif stream.Length > _CAPTURE_LIMIT:
                done(_error("capture_too_large"))
            else:
                done(_capture_result(bytes(stream.ToArray()), "webview2"))
        finally:
            stream.Dispose()

    _windows_task(task, lambda: complete(True), lambda: complete(False))


def _qt_dispatcher(native):
    """Queue operations to the QWebEngineView thread using a typed Qt signal."""
    from qtpy.QtCore import QObject, Qt, Signal, Slot

    class Dispatcher(QObject):
        call = Signal(object)

        def __init__(self):
            super().__init__()
            self.moveToThread(native.thread())
            self.call.connect(self.run, Qt.ConnectionType.QueuedConnection)

        @Slot(object)
        def run(self, action):
            action()

    return Dispatcher()


def _qt_capture(view, done):
    """Grab the owned Qt WebView widget, preserving its on-screen content."""
    from qtpy.QtCore import QByteArray, QBuffer, QIODevice, Qt

    ratio = view.devicePixelRatioF()
    if view.width() * view.height() * ratio ** 2 > 8_000_000:
        done(_error("capture_too_large"))
        return
    pixmap = view.grab()
    if pixmap.isNull():
        done(_error("capture_failed"))
        return
    if pixmap.width() > 1280:
        pixmap = pixmap.scaledToWidth(1280, Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not pixmap.save(buffer, "PNG"):
            done(_error("capture_failed"))
            return
        done(_capture_result(bytes(data), "qt-webengine"))
    finally:
        buffer.close()


class _WindowAutomation:
    """One serialized native surface with bounded calls and same-origin checks."""

    def __init__(self, window, origin):
        self.window = window
        self.origin = _owned_origin(origin)
        self.lock = Lock()
        self.dispatcher = None

    def _dispatch(self, start):
        """Schedule the closure without reading or mutating widgets on the HTTP thread."""
        native = self.window.native
        if native is None:
            raise RuntimeError("Window not ready")
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper
            AppHelper.callAfter(start)
        elif sys.platform == "win32" and hasattr(native, "webview") and hasattr(native, "BeginInvoke"):
            from System import Action
            native.BeginInvoke(Action(start))
        elif sys.platform.startswith("linux") and hasattr(native, "webview") and hasattr(native, "thread"):
            if self.dispatcher is None:
                self.dispatcher = _qt_dispatcher(native)
            self.dispatcher.call.emit(start)
        else:
            raise NotImplementedError("Desktop renderer unavailable")

    def _view_and_url(self):
        """Read the current top-level URL from the native widget on its UI thread."""
        if sys.platform == "darwin":
            view = _macos_view(self.window)
            url = view.URL()
            return view, str(url.absoluteString()) if url is not None else ""
        view = self.window.native.webview
        if sys.platform == "win32":
            if not hasattr(view, "CoreWebView2"):
                raise NotImplementedError("WebView2 unavailable")
            url = view.Source
            return view, str(url.AbsoluteUri) if url is not None else ""
        if not hasattr(view, "page") or not hasattr(view.page(), "runJavaScript"):
            raise NotImplementedError("Qt WebEngine unavailable")
        return view, view.url().toString()

    def _evaluate(self, view, script, done):
        """Use raw native JS APIs to preserve the application's strict CSP."""
        if sys.platform == "darwin":
            view.evaluateJavaScript_completionHandler_(script, lambda result, error: done(result if not error else None))
        elif sys.platform == "win32":
            _windows_script(view, script, done)
        else:
            view.page().runJavaScript(script, done)

    def _request(self, deadline, start):
        """Run one native step before the shared deadline and ignore a late result."""
        ready = Event()
        result = None

        def complete(value):
            nonlocal result
            result = value
            ready.set()

        def invoke():
            if time.monotonic() >= deadline:
                return
            try:
                view, url = self._view_and_url()
                if self.origin is None or _owned_origin(url) != self.origin:
                    complete(_error("window_origin_mismatch"))
                    return
                start(view, url, complete)
            except NotImplementedError:
                complete(_error("renderer_unsupported"))
            except Exception:
                _LOG.debug("Desktop automation step failed", exc_info=True)
                complete(_error("native_operation_failed"))

        try:
            self._dispatch(invoke)
        except NotImplementedError:
            return _error("renderer_unsupported")
        except Exception:
            return _error("window_unavailable")
        if not ready.wait(max(0, deadline - time.monotonic())):
            return _error("window_timeout")
        return result

    def _script(self, deadline, script):
        result = self._request(deadline, lambda view, _url, done: self._evaluate(view, script, done))
        if isinstance(result, dict):
            return result
        if not isinstance(result, str) or len(result.encode("utf-8")) > _SCRIPT_LIMIT:
            return _error("window_script_failed")
        try:
            value = json.loads(result)
        except ValueError:
            return _error("window_script_failed")
        return value if isinstance(value, dict) else _error("window_script_failed")

    def _command(self, deadline, operation, payload):
        origin = json.dumps(self.origin)
        token = json.dumps(uuid4().hex)
        arguments = json.dumps(payload, ensure_ascii=True)
        invocation = "bridge.inspect()" if operation == "inspect" else f"bridge.execute({arguments})"
        # Script source is fixed apart from quoted JSON data and the allowlisted invocation.
        begin = f"""(() => {{
          if (location.origin !== {origin}) return '{{"status":"error","error":"window_origin_mismatch"}}';
          const bridge = window.atlasDesktopAutomation;
          if (!bridge) return '{{"status":"error","error":"window_bridge_unavailable"}}';
          const slot = {{id: {token}, result: null}};
          window.__atlasDesktopAutomationResult = slot;
          Promise.resolve().then(() => {invocation}).then(value => {{
            slot.result = value;
          }}).catch(() => {{
            slot.result = {{status:"error",error:"window_command_failed"}};
          }});
          setTimeout(() => {{
            if (window.__atlasDesktopAutomationResult === slot) delete window.__atlasDesktopAutomationResult;
          }}, 35000);
          return '{{"status":"pending"}}';
        }})()"""
        accepted = self._script(deadline, begin)
        if accepted.get("status") != "pending":
            return accepted
        poll = f"""(() => {{
          if (location.origin !== {origin}) return '{{"status":"error","error":"window_origin_mismatch"}}';
          const slot = window.__atlasDesktopAutomationResult;
          if (slot?.id !== {token}) return '{{"status":"error","error":"window_context_changed"}}';
          if (slot.result === null) return '{{"status":"pending"}}';
          delete window.__atlasDesktopAutomationResult;
          return JSON.stringify(slot.result);
        }})()"""
        while time.monotonic() < deadline:
            result = self._script(deadline, poll)
            if result.get("status") != "pending":
                return result
            time.sleep(0.04)
        return _error("window_timeout")

    def _return_to_map(self, deadline, payload):
        """Complete only after the destination map commits the expected scope."""
        result = self._command(deadline, "execute", payload)
        if not result.get("navigation") and result.get("error") != "window_context_changed":
            return result
        while time.monotonic() < deadline:
            state = self._command(deadline, "inspect", {})
            if (
                state.get("route", {}).get("path") == "/"
                and state.get("projectId") == payload.get("projectId")
                and state.get("snapshotId") == payload.get("snapshotId")
            ):
                return {"status": "ok", "state": state}
            if state.get("error") not in {
                None, "window_bridge_unavailable", "window_context_changed", "window_script_failed",
            }:
                return state
            time.sleep(0.08)
        return _error("window_timeout")

    def _capture_state(self, deadline, payload):
        """Fence capture against the expected project, snapshot and observed map revision."""
        state = self._command(deadline, "inspect", {})
        if state.get("status") == "error":
            return state
        for field in ("projectId", "snapshotId", "revision"):
            if field in payload and state.get(field) != payload[field]:
                return _error("window_scope_changed")
        return state

    def _capture(self, deadline, payload):
        capture = {"darwin": _macos_capture, "win32": _windows_capture}.get(sys.platform, _qt_capture)

        def start(view, url, done):
            def complete(result):
                # Recheck after asynchronous capture, on the UI thread of the same widget.
                def validate():
                    try:
                        current_view, current_url = self._view_and_url()
                        if current_view != view or current_url != url:
                            done(_error("window_context_changed"))
                        else:
                            done(result)
                    except Exception:
                        done(_error("window_unavailable"))

                if time.monotonic() < deadline:
                    self._dispatch(validate)

            capture(view, complete)

        before = self._capture_state(deadline, payload)
        if before.get("status") == "error":
            return before
        result = self._request(deadline, start)
        if result.get("status") != "ok":
            return result
        after = self._capture_state(deadline, payload)
        if after.get("status") == "error":
            return after
        if before.get("revision") != after.get("revision"):
            return _error("window_scope_changed")
        return result

    def __call__(self, operation, payload):
        if operation not in {"inspect", "execute", "capture"}:
            return _error("unknown_window_operation")
        if not self.lock.acquire(blocking=False):
            return _error("window_busy")
        try:
            deadline = time.monotonic() + _TIMEOUT
            if operation == "capture":
                return self._capture(deadline, payload)
            if operation == "execute" and payload.get("action") == "return_to_map":
                return self._return_to_map(deadline, payload)
            return self._command(deadline, operation, payload)
        finally:
            self.lock.release()


def bind_window(window, origin):
    """Return a native handler for one owned Atlas desktop window."""
    return _WindowAutomation(window, origin)
