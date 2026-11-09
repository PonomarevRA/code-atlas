"""Атомарные manifest-файлы и SQLite-индекс неизменяемых ревизий."""

import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .models import encode, validate_tree


def atomic_json(path, value):
    """Публикует один JSON через rename; предыдущий файл сохраняется при ошибке."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(encode(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    """Читает только уже выбранный служебный manifest."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_revision(directory, revision_id, tree, metadata):
    """Строит staging-индекс и публикует каталог один раз по content key."""
    validate_tree(tree)
    directory = Path(directory)
    staging = directory.with_name(directory.name + '.' + uuid4().hex + '.staging')
    staging.mkdir(parents=True)
    connection = sqlite3.connect(staging / 'index.sqlite')
    try:
        connection.execute('CREATE TABLE nodes (id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, position INTEGER, label TEXT, text TEXT, payload TEXT)')
        connection.execute('CREATE INDEX children ON nodes(parent_id, position)')
        connection.execute('CREATE TABLE edges (payload TEXT)')
        identifiers = {node['id']: revision_id + ':' + node['id'] for node in tree['nodes']}
        for original in tree['nodes']:
            node = dict(original)
            node['localId'] = node['id']
            node['id'] = identifiers[node['id']]
            node['parentId'] = identifiers.get(node.get('parentId'))
            node['revisionId'] = revision_id
            if node.get('attributes', {}).get('cellContainerId') in identifiers:
                node['attributes'] = {**node['attributes'], 'cellContainerId': identifiers[node['attributes']['cellContainerId']]}
            connection.execute('INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)', (node['id'], node['parentId'], node['kind'], node.get('order', 0), node.get('label', ''), node.get('text', ''), encode(node)))
        for original in tree.get('edges', []):
            edge = dict(original)
            edge['source'], edge['target'] = identifiers[edge['source']], identifiers[edge['target']]
            connection.execute('INSERT INTO edges VALUES (?)', (encode(edge),))
        connection.commit()
        atomic_json(staging / 'metadata.json', {**metadata, 'revisionId': revision_id, 'coverage': tree.get('coverage'), 'warnings': tree.get('warnings', []), 'nodeCount': len(tree['nodes'])})
    finally:
        connection.close()
    if directory.exists():
        import shutil
        shutil.rmtree(staging)
    else:
        os.replace(staging, directory)
