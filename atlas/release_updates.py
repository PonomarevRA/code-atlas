"""Background release discovery and an explicit local reminder claim after an agent task."""

import json
import logging
import math
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
from time import time
from uuid import uuid4

from desktop_support.settings import read_installation
from desktop_support.updates import MAX_NOTE_LINES, check_release, plain_note, releases_url, version_tuple

from .runtime_paths import APP_VERSION, default_data_dir, resource_root


CHECK_INTERVAL = 6 * 60 * 60
RETRY_INTERVAL = 60 * 60
NOTICE_INTERVAL = 3 * 24 * 60 * 60
MAX_CACHE_AGE = 7 * 24 * 60 * 60
_MAX_STATE_BYTES = 8192
_APP_ID = 'code-atlas'
_PRIVATE_RELEASES_URL = 'https://github.com/PonomarevRA/code-atlas-private/releases'
_STATE_NAME = 'release-update-state.json'
_LOG = logging.getLogger(__name__)


def edition_releases_url():
    """Choose the release listing for this packaged edition."""
    if (resource_root() / 'PUBLIC_SOURCE_MANIFEST.json').is_file():
        return releases_url(_APP_ID)
    return _PRIVATE_RELEASES_URL


def installed_update_enabled(data_directory):
    """Enable automatic networking only for the configured, frozen installation and its data."""
    if not getattr(sys, 'frozen', False):
        return False
    try:
        installation = read_installation(_APP_ID, data_directory)
        executable = Path(sys.executable).resolve()
        if installation.application_path:
            return (executable.is_relative_to(installation.application_path)
                    and installation.data_directory == data_directory)
        if sys.platform != 'darwin' or data_directory != default_data_dir(environ={}).resolve():
            return False
        # Older macOS installers did not create the AGDesktop locator.
        for directory in (Path.home() / 'Applications', Path('/Applications')):
            bundle = directory / 'Code Atlas.app' / 'Contents' / 'MacOS'
            if executable.parent == bundle.resolve() and executable.name == 'CodeAtlas':
                return True
        return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _timestamp(value):
    """Recognize a nonnegative finite timestamp in persisted local state."""
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 10**12


def _notice_release(release, current_version, *, private):
    """Retain only bounded release facts whose identity and installer match the app."""
    if not isinstance(release, dict):
        return None
    if not private and release.get('installerAvailable') is not True:
        return None
    tag = release.get('version')
    index = releases_url(_APP_ID)
    if (not isinstance(tag, str) or version_tuple(tag) <= version_tuple(current_version)
            or release.get('url') != f'{index}/tag/{tag}'):
        return None
    source_notes = release.get('localizedNotes')
    if not isinstance(source_notes, dict):
        source_notes = {}
    notes = {}
    for language in ('ru', 'en'):
        raw = source_notes.get(language)
        if isinstance(raw, list):
            notes[language] = [plain_note(line) for line in raw[:MAX_NOTE_LINES] if isinstance(line, str)]
        else:
            notes[language] = []
    return {'version': tag, 'url': f'{index}/tag/{tag}', 'releasesUrl': index,
            'installerAvailable': release.get('installerAvailable') is True, 'localizedNotes': notes}


class ReleaseUpdates:
    """Own one background checker and a cooldown shared by clients of this Atlas instance."""

    def __init__(self, data_directory, *, enabled=None, current_version=APP_VERSION,
                 checker=check_release, clock=time, private=None):
        """Load a small local cache; explicit test dependencies never require network access."""
        data_directory = Path(data_directory).resolve()
        self.path = data_directory / _STATE_NAME
        self.enabled = installed_update_enabled(data_directory) if enabled is None else enabled
        self.current_version = current_version
        self.private = edition_releases_url() == _PRIVATE_RELEASES_URL if private is None else private
        self.checker, self.clock = checker, clock
        self.lock, self.stop = Lock(), Event()
        self.worker = None
        self.state = self._load() if self.enabled else {}

    def _load(self):
        """Return validated local state, tolerating missing or obsolete metadata."""
        try:
            if self.path.is_symlink():
                return {}
            with self.path.open('rb') as source:
                content = source.read(_MAX_STATE_BYTES + 1)
            if len(content) > _MAX_STATE_BYTES:
                return {}
            value = json.loads(content)
            if not isinstance(value, dict) or value.get('schemaVersion') != 1:
                return {}
            for field in ('checkedAt', 'attemptedAt', 'noticedAt'):
                if field in value and not _timestamp(value[field]):
                    return {}
            release = _notice_release(value.get('release'), self.current_version, private=self.private)
            return {field: value[field] for field in ('checkedAt', 'attemptedAt', 'noticedAt')
                    if field in value} | {'release': release}
        except (OSError, ValueError, TypeError, OverflowError):
            return {}

    def _save(self, state):
        """Publish state atomically before a caller claims a notice."""
        if self.path.is_symlink():
            raise OSError('Release update state is redirected')
        temporary = self.path.with_name(f'.{self.path.name}.{uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps({'schemaVersion': 1, **state}, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self):
        """Schedule periodic checks outside startup and all MCP request paths."""
        if self.enabled and self.worker is None:
            self.worker = Thread(target=self._run, name='atlas-release-check', daemon=True)
            try:
                self.worker.start()
            except RuntimeError:
                self.worker = None
                _LOG.debug('Cannot start GitHub release checker', exc_info=True)

    def close(self):
        """Signal the checker to stop without delaying application shutdown."""
        self.stop.set()

    def _run(self):
        """Keep a daily-scale check interval and a separate offline retry interval."""
        if self.stop.wait(15):
            return
        while not self.stop.is_set():
            self.check_if_due()
            if self.stop.wait(RETRY_INTERVAL):
                return

    def check_if_due(self):
        """Refresh cached metadata outside the claim lock; keep valid cache on network failure."""
        if not self.enabled or self.stop.is_set():
            return
        now = self.clock()
        with self.lock:
            for field, interval in (('checkedAt', CHECK_INTERVAL), ('attemptedAt', RETRY_INTERVAL)):
                previous = self.state.get(field)
                if previous is not None and 0 <= now - previous < interval:
                    return
            self.state['attemptedAt'] = now
        try:
            release = _notice_release(self.checker(_APP_ID, self.current_version), self.current_version, private=self.private)
        except (OSError, ValueError, KeyError, TypeError):
            _LOG.debug('GitHub release metadata unavailable', exc_info=True)
            return
        with self.lock:
            if self.stop.is_set():
                return
            state = {**self.state, 'checkedAt': self.clock(), 'release': release}
            try:
                self._save(state)
            except OSError:
                _LOG.debug('Cannot persist GitHub release cache', exc_info=True)
            self.state = state

    def claim(self, locale):
        """Return at most one cached reminder per three days; perform no external request."""
        if not self.enabled:
            return {'notice': None}
        now = self.clock()
        with self.lock:
            release = self.state.get('release')
            checked = self.state.get('checkedAt')
            noticed = self.state.get('noticedAt')
            if (not release or checked is None or not 0 <= now - checked <= MAX_CACHE_AGE
                    or (noticed is not None and now - noticed < NOTICE_INTERVAL)):
                return {'notice': None}
            state = {**self.state, 'noticedAt': now}
            try:
                self._save(state)
            except OSError:
                _LOG.debug('Cannot persist GitHub release cooldown', exc_info=True)
                return {'notice': None}
            self.state = state
            notes = release['localizedNotes']
            listing = _PRIVATE_RELEASES_URL if self.private else release['releasesUrl']
            return {'notice': {'version': release['version'], 'currentVersion': self.current_version,
                               'edition': 'private' if self.private else 'public',
                               'releaseSource': 'public',
                               'installerAvailable': None if self.private else True,
                               'url': listing if self.private else release['url'], 'releasesUrl': listing,
                               'notes': notes[locale] or notes['en'] or notes['ru']}}
