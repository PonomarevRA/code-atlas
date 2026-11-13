"""Native tray ownership independent of HTTP service implementations."""

import logging
from pathlib import Path
import sys
from threading import Lock, Thread
import webbrowser

from .settings import read_installation
from .updates import check_release, releases_url, version_tuple


class BackgroundHandle:
    """Hide only after native registration succeeds; explicit Quit bypasses this policy."""
    def __init__(self, window, app_id, title, origin, on_quit, open_activity, data_directory, current_version, releases_index=None):
        """Retain native objects and callbacks for exactly one desktop owner."""
        self.window, self.app_id, self.title = window, app_id, title
        self.origin, self.on_quit, self.open_activity = origin, on_quit, open_activity
        self.ready = self.closed = False
        self.native = None
        self.settings = read_installation(app_id, data_directory or Path.home() / '.local/share' / app_id)
        self.data_directory = Path(data_directory) if data_directory is not None else self.settings.data_directory
        self.current_version = current_version
        self.public_releases = releases_url(app_id)
        self.releases_index = releases_index or self.public_releases
        self.update_lock = Lock()
        self.installing = False
        self.dispatch = lambda callback: callback()
        window.events.shown += self.install

    def install(self):
        """Attach once after renderer startup; unsupported trays retain normal close-to-quit behavior."""
        if self.ready or self.closed or self.installing:
            return
        try:
            self.installing = True
            from .tray_platforms import install_tray
            install_tray(self)
        except Exception:
            logging.getLogger(__name__).exception('Native tray unavailable; window close will quit')

    def show(self):
        """Restore the existing window without starting another backend."""
        if self.native and hasattr(self.native, "show"):
            self.dispatch(self.native.show)
        else:
            self.window.show()
            self.window.restore()

    def activity(self):
        """Open service-owned request history; fallback shows a bounded widget snapshot page."""
        if self.open_activity:
            self.open_activity()
        else:
            self.window.load_url(self.origin.rstrip('/') + '/api/desktop/v1/widget')
        self.show()

    def storage(self):
        """Display actual configured folders; runtime data movement is intentionally separate."""
        message = f'Artifacts: {self.data_directory}\nBackups: {self.settings.backup_directory}'
        self.window.create_confirmation_dialog(self.title + ' — Storage', message)

    def releases(self):
        """Open the pinned release listing without requesting metadata or starting an installer."""
        if not self.closed:
            webbrowser.open(self.releases_index)

    def updates(self):
        """Start one manual update worker without blocking the native menu callback."""
        if self.closed or not self.update_lock.acquire(blocking=False):
            return
        try:
            Thread(target=self._check_updates, name='desktop-update-check', daemon=True).start()
        except BaseException:
            self.update_lock.release()
            raise

    def _check_updates(self):
        """Fetch a bounded release summary and open its page only after confirmation."""
        try:
            try:
                result = check_release(self.app_id, self.current_version)
                message = self._update_message(result)
                url = result['url'] if self.releases_index == self.public_releases else self.releases_index
            except Exception:
                logging.getLogger(__name__).exception('Update check failed')
                message = f'Installed version: {self.current_version}\nCould not check for updates.'
                url = self.releases_index
            if not self.closed and self.window.create_confirmation_dialog(
                self.title + ' — Updates', message + '\n\nOpen GitHub Releases?'
            ) and not self.closed:
                webbrowser.open(url)
        except Exception:
            logging.getLogger(__name__).exception('Native update result could not be displayed')
        finally:
            self.update_lock.release()

    def _update_message(self, result):
        """Format installed/latest versions and plain-text release highlights for the dialog."""
        lines = [f'Installed version: {self.current_version}']
        if self.releases_index != self.public_releases:
            lines.extend((f'Latest public release: {result["version"]}', 'Open GitHub Releases to check this edition.'))
        else:
            lines.append(f'Latest release: {result["version"]}')
            if result['available']:
                lines.append('A native update is available.')
            elif version_tuple(result['version']) > version_tuple(self.current_version):
                lines.append('A newer release exists. A native installer is not available for this platform yet.')
            else:
                lines.append('The installed version is up to date.')
        notes = result.get('notes') or ()
        if notes:
            lines.extend(('', 'What’s new', *(f'• {note}' for note in notes[:4])))
        return '\n'.join(lines)

    def hide_on_close(self):
        """Return true only when an accessible registered tray can restore the window."""
        if not self.ready or self.closed or not self.settings.close_to_tray:
            return False
        if self.native and hasattr(self.native, "hide"):
            self.dispatch(self.native.hide)
        else:
            self.window.hide()
        return True

    def close(self):
        """Remove the native icon on its owning UI thread, even after partial installation."""
        if self.closed:
            return
        self.closed = True
        self.ready = False
        self.window.events.shown -= self.install
        if self.native:
            self.dispatch(self.native.close)


def bind_background(window, *, app_id, title, origin, on_quit, open_activity=None, data_directory=None,
                    current_version="0.1.0", releases_index=None):
    """Return an immediately usable lifecycle hook; native readiness follows the shown event."""
    return BackgroundHandle(window, app_id, title, origin, on_quit, open_activity, data_directory,
                            current_version, releases_index)
