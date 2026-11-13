"""Exercise raw byte fences and immutable overlay epochs with actual linked worktrees."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from atlas import store
from atlas.server import create_app
from atlas.worktree_overlay_git import capture_delta, clean_stamp, identity
from test_jobs import fake_build


def git(root, *args):
    """Run fixture Git commands without reading user configuration."""
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


@pytest.fixture
def trees(tmp_path):
    """Provide a clean base and two linked worktrees at its exact commit."""
    base, left, right = (tmp_path / name for name in ('base', 'left', 'right'))
    base.mkdir()
    git(base, 'init')
    (base / 'service.py').write_text('def first():\n    return 1\n')
    (base / '.gitattributes').write_text('service.py text eol=lf\n')
    git(base, 'add', '.')
    git(base, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        '-c', 'commit.gpgSign=false', 'commit', '-m', 'fixture')
    for worktree in (left, right):
        git(base, 'worktree', 'add', '--detach', str(worktree), 'HEAD')
    return base, left, right


def test_raw_delta_sees_bytes_hidden_by_git_normalization(trees):
    """Line-ending conversion cannot silently hide a source-byte change."""
    base, left, _ = trees
    stamp = clean_stamp(base)
    (left / 'service.py').write_bytes(b'def first():\r\n    return 1\r\n')
    assert not git(left, 'diff', '--name-only', 'HEAD')
    changes = capture_delta(left, identity(left), stamp['fileHashes'])
    assert changes['service.py'][0]['status'] == 'modified'
    assert changes['service.py'][0]['sourceHash'] != stamp['fileHashes']['service.py']


def test_capture_never_calls_configured_git_filters(trees):
    """A worktree filter installed in repository config cannot run during a read."""
    base, left, _ = trees
    stamp = clean_stamp(base)
    sentinel = left / 'ran-filter'
    git(base, 'config', 'filter.pilot.clean', f'touch "{sentinel}"; cat')
    (left / '.gitattributes').write_text('service.py filter=pilot\n')
    (left / 'service.py').write_text('def changed():\n    return 2\n')
    changes = capture_delta(left, identity(left), stamp['fileHashes'])
    assert set(changes) == {'.gitattributes', 'service.py'}
    assert not sentinel.exists()


def test_revision_pins_parallel_reads_and_masks_base_declarations(tmp_path, trees, monkeypatch):
    """Exercise route wiring, explicit refresh, sibling isolation, and base retirement."""
    base, left, right = trees
    monkeypatch.setattr(store, 'build_graph', fake_build)
    app = create_app(tmp_path / 'atlas-data')
    app.state.references.start_initial_indexing = lambda: None
    path = '/api/checkout-sessions'
    with TestClient(app, base_url='http://127.0.0.1') as client:
        created = client.post(path, json={'checkout': str(base), 'ownerKey': 'overlay-test', 'overlayBase': True})
        assert created.status_code == 202, created.text
        session = created.json()['sessionId']
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            project = client.get(f'{path}/{session}').json()['project']
            if project['status'] in {'completed', 'failed'}:
                break
            time.sleep(0.01)
        assert project['status'] == 'completed', project

        def open_overlay(root):
            """Open one sibling at the same certified snapshot."""
            response = client.post(f'{path}/overlays', json={'checkout': str(root),
                'baseSessionId': session, 'baseSnapshotId': project['snapshotId']})
            assert response.status_code == 200, response.text
            return response.json()

        def search(handle, query):
            """Read the captured epoch even if the target now has unrefreshed edits."""
            return client.post(f"{path}/overlays/{handle['overlayId']}/search", json={
                'query': query, 'overlayRevision': handle['overlayRevision'], 'limit': 5})

        (left / 'service.py').write_text('def left_only():\n    return 2\n')
        (right / 'extra.py').write_text('def right_only():\n    return 3\n')
        a, b = open_overlay(left), open_overlay(right)
        assert not search(a, 'first').json()['nodes']
        assert any(row['id'] == 'first' for row in search(b, 'first').json()['nodes'])
        assert search(a, 'left_only').json()['declarations'][0]['file'] == 'service.py'
        assert not search(b, 'left_only').json()['declarations']

        (left / 'service.py').unlink()
        (left / 'package.json').write_text('{"type":"module"}')
        with ThreadPoolExecutor(max_workers=9) as executor:
            reads = [executor.submit(search, b, 'right_only') for _ in range(8)]
            new = client.post(f"{path}/overlays/{a['overlayId']}/refresh", json={}).json()
        assert all(row.result().json()['declarations'][0]['name'] == 'right_only' for row in reads)
        assert new['overlayRevision'] != a['overlayRevision']
        assert search(a, 'left_only').status_code == 409
        changed = search(new, 'first').json()
        assert not changed['nodes'] and changed['rebindScope'] == 'repository'
        assert {row['file']: row['status'] for row in changed['changedFiles']} == {
            'package.json': 'added', 'service.py': 'deleted'}
        client.delete(f'{path}/{session}')
        assert search(b, 'right_only').status_code == 410
