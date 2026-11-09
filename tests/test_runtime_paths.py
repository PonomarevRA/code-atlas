"""Обновляемое приложение не владеет пользовательскими индексами и знаниями."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile
import pytest
from atlas.runtime_paths import default_data_dir,managed_repositories_dir,migrate_legacy_data,ensure_data_schema,backup_data
from atlas.process_lock import ProcessLock
from atlas.store import ProjectStore, write_json
from atlas.mcp_graph import build_mcp_graph
from test_grouping_cache import legacy_store


def test_platform_user_data_defaults_and_explicit_override(tmp_path):
    """Пути выбираются по ОС; относительный XDG не переносит данные в cwd приложения."""
    assert default_data_dir('darwin',{},tmp_path)==tmp_path/'Library/Application Support/Code Atlas'
    assert default_data_dir('win32',{'LOCALAPPDATA':str(tmp_path/'local')},tmp_path)==tmp_path/'local/Code Atlas'
    assert default_data_dir('linux',{'XDG_DATA_HOME':'relative'},tmp_path)==tmp_path/'.local/share/code-atlas'
    assert default_data_dir('linux',{'ATLAS_DATA_DIR':str(tmp_path/'chosen')},tmp_path)==tmp_path/'chosen'
    assert managed_repositories_dir(tmp_path/'data')==tmp_path/'data-repositories'


def test_migration_preserves_user_files_and_remaps_only_internal_paths(tmp_path):
    """Перенесённый реестр открывается, raw/MCP пути рабочие, notes/sourcepath остаются прежними."""
    state,project_id,source_path,graph=legacy_store(tmp_path)
    source=state.data_dir
    mcp_path = source_path.with_name('mcp-graph.json')
    write_json(mcp_path, build_mcp_graph(graph))
    state.projects[project_id]['_mcpGraph'] = str(mcp_path)
    write_json(state.registry, state.projects)
    state.close()
    (source/'knowledge').mkdir()
    notes=b'# Knowledge\nDo not rewrite /old/path.\n'
    (source/'knowledge/page.md').write_bytes(notes)
    (source/'knowledge/custom.json').write_bytes(b'not-json user data')
    (source/'views').mkdir()
    view={'id':'a'*32,'text':'Authored note','projectPath':str(tmp_path),'layout':{'x':12}}
    (source/'views/view.json').write_text(json.dumps(view))
    originals={path.relative_to(source):path.read_bytes() for path in source.rglob('*') if path.is_file()}
    target=tmp_path/'new-data'
    migrate_legacy_data(source,target)
    for relative,content in originals.items():
        assert (source/relative).read_bytes()==content
    assert (target/'knowledge/page.md').read_bytes()==notes
    assert (target/'knowledge/custom.json').read_bytes()==b'not-json user data'
    assert json.loads((target/'views/view.json').read_text())==view
    migrated=ProjectStore(target)
    try:
        assert migrated.projects[project_id]['path']==str(tmp_path)
        assert migrated.raw_graph(project_id).is_relative_to(target)
        assert migrated.published_mcp_graph(project_id).is_relative_to(target)
        assert migrated.published_mcp_graph(project_id).is_file()
        _,snapshot=migrated.comparison_snapshot(project_id)
        assert Path(snapshot['metadata']['rawGraphPath']).is_relative_to(target)
        assert Path(snapshot['metadata']['rawGraphPath']).is_file()
    finally:
        migrated.close()
    with pytest.raises(RuntimeError):
        migrate_legacy_data(source,target)
    assert (target/'knowledge/page.md').read_bytes()==notes


def test_migration_failure_and_active_lock_never_publish_partial_copy(tmp_path,monkeypatch):
    """Неудачный copy и работающий Atlas оставляют источник и назначение без частичного переноса."""
    source=tmp_path/'old';source.mkdir();(source/'keep').write_text('original')
    target=tmp_path/'new'
    with ProcessLock(source/'server.lock'):
        with pytest.raises(RuntimeError):
            migrate_legacy_data(source,target)
    def broken(*args,**kwargs):
        """Отказ диска посередине копирования."""
        raise OSError('disk failure')
    monkeypatch.setattr('atlas.runtime_paths.shutil.copytree',broken)
    with pytest.raises(OSError):
        migrate_legacy_data(source,target)
    assert not target.exists()
    assert not list(tmp_path.glob('.code-atlas-migration-*'))
    assert (source/'keep').read_text()=='original'


def test_future_schema_rejected_backup_complete_and_non_destructive(tmp_path):
    """Новая схема не затирается старой программой; backup включает views/knowledge."""
    data=tmp_path/'data';data.mkdir()
    marker=data/'data-schema.json';marker.write_text('{"schemaVersion":999}')
    with pytest.raises(RuntimeError):ensure_data_schema(data)
    assert json.loads(marker.read_text())['schemaVersion']==999
    (data/'knowledge').mkdir();(data/'knowledge/note.md').write_text('knowledge')
    (data/'views').mkdir();(data/'views/x.json').write_text('{}')
    archive=backup_data(data)
    with zipfile.ZipFile(archive) as z:
        assert 'server.lock' not in z.namelist()
        assert z.read('knowledge/note.md')==b'knowledge'
        assert z.read('views/x.json')==b'{}'
    assert (data/'knowledge/note.md').is_file()


def test_backup_and_migration_preserve_mixed_map_layouts(tmp_path):
    """Копии сохраняют оба layout и авторские байты; перенос меняет только служебные пути."""
    source = tmp_path / 'mixed-data'
    snapshot = Path('maps/projects') / ('a' * 16) / ('b' * 32)
    graph = source / snapshot / 'atlas-graph.json'
    raw_graph = source / snapshot / 'graphify-out/graph.json'
    authored = b'{ "note": "Keep authored spacing", "layout": { "x": 12 } }\n'
    files = {
        Path('views') / ('c' * 32 + '.json'): authored,
        Path('maps/views') / ('d' * 32 + '.json'): authored,
        Path('data-maps/legacy/views/view.json'): authored,
        Path('maps/data-maps/current/views/view.json'): authored,
        snapshot / 'graphify-out/graph.json': b'{"nodes": [], "links": []}',
        snapshot / 'atlas-graph.json': json.dumps({
            'metadata': {'rawGraphPath': str(raw_graph)},
            'note': str(source / 'authored-reference'),
        }).encode(),
        Path('projects.json'): json.dumps({'a' * 16: {
            '_graph': str(graph), '_rawGraph': str(raw_graph),
            'path': str(tmp_path / 'repository'),
        }}).encode(),
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    archive = backup_data(source)
    with zipfile.ZipFile(archive) as saved:
        for relative, content in files.items():
            assert saved.read(relative.as_posix()) == content

    target = tmp_path / 'migrated-data'
    migrate_legacy_data(source, target)
    for relative, content in files.items():
        assert (source / relative).read_bytes() == content
        if relative not in {Path('projects.json'), snapshot / 'atlas-graph.json'}:
            assert (target / relative).read_bytes() == content
    registry = json.loads((target / 'projects.json').read_text())['a' * 16]
    assert registry['_graph'] == str(target / snapshot / 'atlas-graph.json')
    assert registry['_rawGraph'] == str(target / snapshot / 'graphify-out/graph.json')
    assert registry['path'] == str(tmp_path / 'repository')
    migrated_graph = json.loads((target / snapshot / 'atlas-graph.json').read_text())
    assert migrated_graph['metadata']['rawGraphPath'] == str(target / snapshot / 'graphify-out/graph.json')
    assert migrated_graph['note'] == str(source / 'authored-reference')
