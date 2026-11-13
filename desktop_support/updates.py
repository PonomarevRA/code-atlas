"""Bounded read-only update discovery pinned to the application's official repository."""

import json
import platform
import re
import sys
import unicodedata
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

REPOSITORIES = {
    'code-atlas': 'code-atlas', 'agmemory': 'ag-memmory', 'ag-observer': 'ag-observer',
    'ag-tasks': 'ag-tasks', 'ag-design': 'ag-design',
}
_TARGETS = {'darwin': ('macos', 'dmg'), 'win32': ('windows', 'exe'), 'linux': ('linux', 'tar.gz')}
_ARCHITECTURES = {'arm64': 'arm64', 'aarch64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}
_NOTE_HEADINGS = {'### Русский': 'ru', '### English': 'en'}
MAX_NOTE_LINES = 5
MAX_NOTE_LENGTH = 200


class NoRedirect(HTTPRedirectHandler):
    """Never follow a metadata response into an unreviewed host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Decline every redirect while retaining urllib's HTTP error handling."""
        return None


def version_tuple(value):
    """Compare stable numeric releases only; reject draft/prerelease naming ambiguity."""
    match = re.fullmatch(r'v?(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})', value)
    if not match:
        raise ValueError('Expected a stable semantic version')
    return tuple(int(part) for part in match.groups())


def releases_url(app_id):
    """Return the allowlisted GitHub release index for one application."""
    return f'https://github.com/PonomarevRA/{REPOSITORIES[app_id]}/releases'


def plain_note(value):
    """Strip links, markup and invisible controls from short untrusted release text."""
    value = re.sub(r'!?\[([^\]\n]*)\]\([^\n)]*\)', r'\1', value)
    value = re.sub(r'<[^>]*>|(?:https?://|www\.)\S+', '', value, flags=re.IGNORECASE)
    value = ''.join(char for char in value if not unicodedata.category(char).startswith('C'))
    return ' '.join(value.split())[:MAX_NOTE_LENGTH].strip()


def release_notes(body, tag):
    """Read only bullets from the selected version's Russian and English sections."""
    result = {'ru': [], 'en': []}
    if not isinstance(body, str):
        return result
    version = tag.removeprefix('v')
    version_heading = f'## [{version}]'
    active, language = False, None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith('## '):
            if active:
                break
            active = line == version_heading or line.startswith(version_heading + ' ')
        elif active and line.startswith('### '):
            language = _NOTE_HEADINGS.get(line)
        elif active and language and line.startswith('- ') and len(result[language]) < MAX_NOTE_LINES:
            note = plain_note(line[2:])
            if note:
                result[language].append(note)
    return result


def _native_installer(release, tag, app_id, target_platform, machine):
    """Match a usable uploaded installer by its exact product, target and download URL."""
    target = _TARGETS.get(target_platform)
    architecture = _ARCHITECTURES.get(machine.lower())
    assets = release.get('assets')
    if target is None or architecture is None or not isinstance(assets, list):
        return False
    os_name, extension = target
    asset_name = f'{app_id}-{tag.removeprefix("v")}-{os_name}-{architecture}.{extension}'
    expected = f'{releases_url(app_id)}/download/{tag}/{asset_name}'
    return any(isinstance(asset, dict) and asset.get('name') == asset_name
               and asset.get('state') == 'uploaded' and type(asset.get('size')) is int and asset['size'] > 0
               and asset.get('browser_download_url') == expected for asset in assets)


def check_release(app_id, current_version, *, target_platform=None, machine=None):
    """Fetch verified stable metadata, qualifying Code Atlas for the current native target."""
    installed = version_tuple(current_version)
    repository = REPOSITORIES[app_id]
    url = f'https://api.github.com/repos/PonomarevRA/{repository}/releases/latest'
    request = Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'AGDesktop/1'})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=8) as response:
        payload = response.read(262145)
    if len(payload) > 262144:
        raise ValueError('Release metadata exceeds limit')
    release = json.loads(payload)
    if not isinstance(release, dict) or not isinstance(release.get('tag_name'), str):
        raise ValueError('Unexpected release metadata')
    tag = release['tag_name']
    latest = version_tuple(tag)
    index = releases_url(app_id)
    expected = f'{index}/tag/{tag}'
    if release.get('html_url') != expected or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('Unexpected release identity')
    compatible = app_id != 'code-atlas' or _native_installer(
        release, tag, app_id, target_platform or sys.platform, machine or platform.machine())
    notes = release_notes(release.get('body'), tag)
    return {'version': tag, 'available': latest > installed and compatible, 'url': expected,
            'releasesUrl': index, 'installerAvailable': compatible,
            'notes': notes['en'] or notes['ru'], 'localizedNotes': notes}
