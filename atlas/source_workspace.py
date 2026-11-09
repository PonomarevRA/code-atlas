"""Read-only indexed-file discovery, using existing snapshot identities without scanning disk."""

from pathlib import PurePosixPath


def indexed_files(graph: dict, snapshot_id: str, query: str = '', offset: int = 0, limit: int = 100) -> dict:
    """Return one deterministic raw node per safe relative path; listed files may since have moved."""
    files = {}
    for node in graph.get('nodes', []):
        name = node.get('file') or node.get('source_file')
        if not isinstance(name, str) or not name or node.get('kind') == 'external' or not node.get('id'):
            continue
        name = name.replace('\\', '/')
        path = PurePosixPath(name)
        if path.is_absolute() or ':' in name or any(part in {'.', '..'} for part in name.split('/')):
            continue
        name = path.as_posix()
        line = node.get('line')
        line = line if type(line) is int and line > 0 else None
        rank = (node.get('kind') != 'file', line is None, line or 0, str(node['id']))
        if name not in files or rank < files[name][0]:
            files[name] = (rank, {'file': name, 'nodeId': node['id'], 'line': line})
    needle = query.strip().casefold()
    matches = [entry for name, (_, entry) in sorted(files.items()) if needle in name.casefold()]
    end = offset + limit
    return {'snapshotId': snapshot_id, 'files': matches[offset:end], 'total': len(matches),
            'nextOffset': end if end < len(matches) else None, 'truncated': end < len(matches)}
