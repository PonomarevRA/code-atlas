"""Prepare real synthetic code/data maps for an explicitly isolated cleanup runtime."""
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import argparse
import hashlib
import json
import time


class NoRedirect(HTTPRedirectHandler):
    """
    Confine fixture requests to the selected local test instance.
    """
    def redirect_request(self, *args, **kwargs):
        """
        Refuse forwarding a synthetic source path or mutation to another origin.
        """
        return None


def prepare(config: dict) -> dict:
    """
    Build and preserve fixture evidence without ever clearing existing user maps.

    Args:
        config: Explicit origin, dataDir and fixtureDir in a marked test-owned profile.
    Returns:
        Map/snapshot IDs, saved-view IDs and source hashes for browser verification.
    """
    origin = config['origin'].rstrip('/')
    address = urlsplit(origin)
    assert address.scheme == 'http' and address.hostname == '127.0.0.1'
    assert address.port not in {8765, 53407}, 'Never target the installed or shared preview server'
    assert not address.username and not address.password and not address.query and not address.fragment
    data = Path(config['dataDir']).resolve()
    fixtures = Path(config['fixtureDir']).resolve()
    assert data.parent == fixtures.parent
    marker = data.parent / '.atlas-map-cleanup-test'
    assert marker.is_file() and marker.read_text() == 'synthetic-fixture-only\n'
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(path, body=None):
        """
        Read one same-origin fixture response or perform an explicitly requested POST.
        """
        payload = None if body is None else json.dumps(body).encode()
        headers = {'Origin': origin, 'Content-Type': 'application/json'}
        with opener.open(Request(origin + path, data=payload, headers=headers), timeout=30) as response:
            return json.load(response)

    def wait_job(path):
        """
        Wait at most two minutes for an owned code/data build, preserving its error status.
        """
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = request(path)
            if job['status'] not in {'queued', 'running'}:
                assert job['status'] == 'completed', {'jobStatus': job['status']}
                return job
            time.sleep(.2)
        raise AssertionError('Synthetic build deadline exceeded')

    projects = request('/api/projects')
    storage = request('/api/technical/storage')
    assert not projects['projects'] and not storage['maps'], 'Fixture setup requires a fresh profile'
    assert Path(storage['storagePath']).resolve() == data
    fixtures.mkdir()
    alpha, beta = fixtures / 'Cleanup Alpha', fixtures / 'Unrelated Beta'
    alpha.mkdir()
    beta.mkdir()
    source = 'raise RuntimeError("SOURCE_MUST_NOT_EXECUTE")\n\ndef greet(name):\n    if name:\n        return "hello " + name\n    return "hello"\n'
    (alpha / 'app.py').write_text(source)
    (alpha / 'orders.csv').write_text('id,name\n1,Ada\n2,Lin\n')
    (beta / 'app.py').write_text('def unrelated():\n    return 42\n')
    records = []
    for directory in [alpha, beta]:
        result = request('/api/projects', {'path': str(directory)})
        wait_job('/api/jobs/' + result['job']['id'])
        project = next(item for item in request('/api/projects')['projects'] if item['id'] == result['project']['id'])
        records.append(project)
    primary, unrelated = records
    found = request('/api/projects/' + primary['id'] + '/search-nodes', {'query': 'greet', 'limit': 5})
    assert found['nodes']
    saved_code = request('/api/views', {'projectId': primary['id'], 'snapshotId': primary['snapshotId'],
                                      'title': 'Synthetic historical code view', 'nodeIds': [found['nodes'][0]['id']],
                                      'depth': 0, 'maxNodes': 1})
    # A second actual build supplies history and project-owned deduplicated objects.
    (alpha / 'app.py').write_text(source.replace('"hello"', '"hello again"'))
    rebuilt = request('/api/projects/' + primary['id'] + '/rebuild', {})
    wait_job('/api/jobs/' + rebuilt['job']['id'])
    primary = next(item for item in request('/api/projects')['projects'] if item['id'] == primary['id'])
    created = request('/api/data-maps', {'projectId': primary['id'], 'title': 'Orders <img src=x onerror=bad>',
                                       'files': ['orders.csv']})
    wait_job('/api/data-jobs/' + created['job']['id'])
    data_map = request('/api/data-maps/' + created['map']['id'])
    schema = request('/api/data-maps/' + data_map['id'] + '/query/schema', {'snapshotId': data_map['snapshotId']})
    collection = next(item['id'] for item in schema['items'] if item['kind'] == 'collection')
    page = request('/api/data-maps/' + data_map['id'] + '/query/records',
                   {'snapshotId': data_map['snapshotId'], 'collectionId': collection, 'limit': 1})
    saved_data = request('/api/data-maps/' + data_map['id'] + '/query/view',
                         {'snapshotId': data_map['snapshotId'], 'recordId': page['items'][0]['id'],
                          'title': 'Synthetic historical data view', 'maxDepth': 1, 'maxNodes': 5})
    protected = {}
    for relative in ['models/keep.txt', 'research/keep.txt', 'document-maps/keep.txt']:
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Unrelated synthetic user data; preserve this file.\n')
        protected[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    deadline = time.monotonic() + 30
    selection = [{'kind': 'code', 'id': primary['id']}, {'kind': 'data', 'id': data_map['id']}]
    while True:
        try:
            ready = request('/api/technical/map-cleanup/preview', {'maps': selection})
            assert ready['status'] == 'ready'
            break
        except HTTPError as error:
            if error.code != 409 or time.monotonic() >= deadline:
                raise
            time.sleep(.2)
    assert (alpha / 'app.py').read_text().startswith('raise RuntimeError("SOURCE_MUST_NOT_EXECUTE")')
    source_hashes = {str(path.relative_to(fixtures)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in fixtures.rglob('*') if path.is_file()}
    return {**config, 'project': primary, 'unrelatedProject': unrelated, 'dataMap': data_map,
            'selection': selection, 'savedCodeViewId': saved_code['id'], 'savedDataViewId': saved_data['id'],
            'protected': protected, 'sourceHashes': source_hashes, 'fixturePrepared': True}


def main():
    """
    Read explicit owned-profile metadata and save a reusable browser fixture context.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(json.loads(args.config.read_text()))
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({'status': 'PASS', 'preparedCodeMaps': 2, 'preparedDataMaps': 1, 'codeHistory': True,
                      'savedViews': 2, 'protectedOtherScopes': len(result['protected'])}))


if __name__ == '__main__':
    main()
