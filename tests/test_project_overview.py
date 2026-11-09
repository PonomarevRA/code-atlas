"""Overview pins captured paths and never silently repairs an unavailable map."""

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.graph_analysis_api import register_graph_analysis_routes
from atlas.project_overview import build_project_overview, capture_overview
from atlas.store import ProjectStore, StoreError
from test_map_cleanup import publish_code, PROJECT, SNAPSHOT


def test_registered_missing_map_does_not_build_and_revision_is_pinned(tmp_path):
    """
    A registration without graph returns useful state; stale requested revisions fail.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    store = ProjectStore(tmp_path / 'state')
    try:
        store.projects[PROJECT] = {'id': PROJECT, 'name': 'CALC', 'status': 'failed'}
        result = store.project_overview(PROJECT)
        assert result['state'] == 'build_failed_no_snapshot'
        assert result['snapshot']['available'] is False
        assert result['analysisStatus'] == 'unavailable'
        assert result['areas'] == [] and store.active_job is None
        with pytest.raises(StoreError) as stale:
            store.project_overview(PROJECT, 'other')
        assert stale.value.status == 409
        store.projects[PROJECT]['status'] = 'not-built'
        assert store.project_overview(PROJECT)['state'] == 'not_built'
    finally:
        store.executor.shutdown()


def test_http_overview_existing_snapshot_and_unknown_legacy_coverage(tmp_path):
    """
    Old snapshots have sourced paths but make no completeness or business-purpose claim.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    source = tmp_path / 'source'
    source.mkdir()
    store = ProjectStore(tmp_path / 'state')
    try:
        publish_code(store, source)
        app = FastAPI()
        register_graph_analysis_routes(app, store)
        client = TestClient(app)
        result = client.get(f'/api/projects/{PROJECT}/overview?snapshotId={SNAPSHOT}').json()
        assert result['state'] == 'ready' and result['analysisStatus'] == 'unknown'
        assert result['purpose']['status'] == 'unknown'
        assert result['areas'][0]['nodeIds'] == ['n']
        assert result['areas'][0]['provenance'][0]['snapshotId'] == SNAPSHOT
    finally:
        store.executor.shutdown()


def test_captured_readme_is_revision_evidence_not_current_read(tmp_path):
    """
    Purpose is an attributed bounded excerpt, independent of later README edits.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    content = b'# Example\n\nComputes captured portfolios.\n'
    (tmp_path / 'README.md').write_bytes(content)
    graph = capture_overview({'nodes': [], 'metadata': {}}, tmp_path)
    (tmp_path / 'README.md').write_text('Changed meaning')
    result = build_project_overview({'id': PROJECT, 'snapshotId': SNAPSHOT}, graph)
    assert result['purpose']['text'] == 'Computes captured portfolios.'
    assert result['purpose']['provenance'][0]['sourceHash'] == sha256(content).hexdigest()
    assert result['purpose']['provenance'][0]['snapshotId'] == SNAPSHOT


def test_roles_areas_and_node_lists_are_bounded():
    """
    Tests/examples/vendor/generated roles are presentation hints over existing IDs.
    """
    nodes = [{'id': f'n{i}', 'file': f'src/area{i}/a.py'} for i in range(40)]
    nodes += [{'id': 'test', 'file': 'tests/a.py'}, {'id': 'vendor', 'file': 'vendor/a.py'}]
    result = build_project_overview({'id': PROJECT, 'snapshotId': SNAPSHOT}, {'nodes': nodes})
    assert len(result['areas']) == 24 and result['coverage']['areasTruncated']
    assert all(set(area['nodeIds']) <= {node['id'] for node in nodes} for area in result['areas'])


def test_manifest_description_precedes_readme_warning(tmp_path):
    """
    An authored project description is stronger evidence than a README publication warning.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    (tmp_path / 'pyproject.toml').write_text('[project]\ndescription="Local component catalog"\n')
    (tmp_path / 'README.md').write_text('Warning: do not publish this repository.\n')
    graph = capture_overview({'nodes': []}, tmp_path)
    purpose = graph['metadata']['projectOverview']['purpose']
    assert purpose['text'] == 'Local component catalog'
    assert purpose['provenance'][0]['kind'] == 'snapshot-captured-manifest-description'


def test_readme_symlink_does_not_capture_outside_content(tmp_path):
    """
    A manifest or README link cannot become captured outside evidence.

    Args:
        tmp_path: Isolated pytest filesystem, unrelated to registered user data.
    """
    root = tmp_path / 'root'
    root.mkdir()
    (tmp_path / 'secret').write_text('Do not expose')
    (root / 'README.md').symlink_to(tmp_path / 'secret')
    assert capture_overview({}, root)['metadata']['projectOverview']['purpose']['status'] == 'unknown'
