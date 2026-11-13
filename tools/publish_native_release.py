"""Publish verified native installer groups to the GitHub Release for a source tag."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


REPOSITORY = 'PonomarevRA/code-atlas'
TARGETS = {'darwin': ('macos', 'dmg'), 'linux': ('linux', 'tar.gz'), 'win32': ('windows', 'exe')}
ARCHITECTURES = {'aarch64': 'arm64', 'arm64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}


def sha256(path):
    """Hash large installers without loading them into memory."""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def installer_groups(directory, version, edition):
    """Verify installer names, provenance metadata and both copies of each digest."""
    groups = []
    for metadata in sorted(directory.glob('*.json')):
        info = json.loads(metadata.read_text())
        os_name, suffix = TARGETS[info['platform']]
        arch = ARCHITECTURES[info['architecture'].lower()]
        name = f'code-atlas-{version}-{os_name}-{arch}.{suffix}'
        asset, checksum = directory / name, directory / (name + '.sha256')
        if (info['schemaVersion'], info['appId'], info['version'], info['edition'], info['asset'],
                info['repository'], metadata.name) != (
                'ag-desktop/release-v1', 'code-atlas', version, edition, name, REPOSITORY, name + '.json'):
            raise ValueError(f'Unexpected installer identity in {metadata.name}')
        expected = info['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or checksum.read_text().split() != [expected, name]:
            raise ValueError(f'Invalid checksum for {name}')
        if sha256(asset) != expected:
            raise ValueError(f'Installer hash mismatch for {name}')
        groups.append((asset, checksum, metadata))
    if not groups:
        raise ValueError('No native installers found')
    return groups


def gh(*args, check=True):
    """Run authenticated GitHub CLI without exposing its credentials."""
    result = subprocess.run(['gh', *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'GitHub CLI failed')
    return result


def release_state(repo, tag):
    """Read an existing release or recognize GitHub's explicit missing-tag response."""
    result = gh('release', 'view', tag, '--repo', repo, '--json', 'isDraft,assets,url', check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if result.stderr.strip().lower() == 'release not found':
        return None
    raise RuntimeError(result.stderr.strip() or 'Could not inspect release')


def verify_existing(repo, tag, group, remote):
    """Check a previously published group before retaining a different build of that target."""
    asset, checksum, metadata = group
    with tempfile.TemporaryDirectory() as directory:
        gh('release', 'download', tag, '--repo', repo, '--dir', directory,
           '--pattern', checksum.name, '--pattern', metadata.name)
        root = Path(directory)
        info = json.loads((root / metadata.name).read_text())
        local = json.loads(metadata.read_text())
        expected = info.pop('sha256')
        local.pop('sha256')
        if (info != local or remote[asset.name] != 'sha256:' + expected
                or (root / checksum.name).read_text().split() != [expected, asset.name]):
            raise ValueError(f'Existing release group failed verification: {asset.name}')


def publish(repo, tag, groups, notes):
    """Create or resume a draft, upload missing assets, then publish verified downloads."""
    release = release_state(repo, tag)
    if release is None:
        gh('release', 'create', tag, '--repo', repo, '--verify-tag', '--draft',
           '--title', f'Code Atlas {tag}', '--notes-file', str(notes))
        release = release_state(repo, tag)
    remote = {asset['name']: asset.get('digest') for asset in release['assets']}
    pending = {}
    for group in groups:
        digests = {path.name: 'sha256:' + sha256(path) for path in group}
        conflicts = [name for name in digests.keys() & remote.keys() if remote[name] != digests[name]]
        if conflicts:
            if not digests.keys() <= remote.keys():
                raise ValueError(f'Existing assets conflict with an incomplete group: {conflicts}')
            verify_existing(repo, tag, group, remote)
            continue
        pending.update({path: digests[path.name] for path in group if path.name not in remote})
    if pending:
        gh('release', 'upload', tag, '--repo', repo, '--', *map(str, pending))
        release = release_state(repo, tag)
        remote = {asset['name']: asset.get('digest') for asset in release['assets']}
        for path, expected in pending.items():
            if remote.get(path.name) != expected:
                raise ValueError(f'GitHub asset verification failed: {path.name}')
    if release['isDraft']:
        gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--latest')
    print(release_state(repo, tag)['url'])


def main():
    """Bind installers to a single edition and the exact GitHub source revision."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, choices=[REPOSITORY, REPOSITORY + '-private'])
    parser.add_argument('--tag', required=True)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--notes', default=Path('docs/native-release-notes.md'), type=Path)
    parser.add_argument('--check', action='store_true', help='Validate assets without contacting GitHub')
    args = parser.parse_args()
    product = json.loads(Path('packaging/desktop-product.json').read_text())
    version = product['version']
    if args.tag != 'v' + version:
        parser.error('Release tag must match packaging/desktop-product.json')
    edition = 'public' if Path('PUBLIC_SOURCE_MANIFEST.json').is_file() else 'private'
    if args.repo != REPOSITORY + ('-private' if edition == 'private' else ''):
        parser.error('Checkout edition does not match the target repository')
    groups = installer_groups(args.assets.resolve(), version, edition)
    if args.check:
        print(f'{len(groups)} verified {edition} installer group(s)')
        return
    if not args.notes.is_file():
        parser.error('Release notes file is missing')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    remote = json.loads(gh('api', f'repos/{args.repo}/commits/{args.tag}').stdout)['sha']
    if commit != remote:
        parser.error('GitHub tag does not resolve to this source commit')
    publish(args.repo, args.tag, groups, args.notes)


if __name__ == '__main__':
    main()
