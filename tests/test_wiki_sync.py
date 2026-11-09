"""Публикация Wiki требует явного флага и сохраняет чужие страницы."""
import importlib.util
import json
from pathlib import Path
import pytest


def wiki_module():
    """Загружает release-скрипт без запуска CLI."""
    path=Path(__file__).parent.parent/'scripts/sync_wiki.py'
    spec=importlib.util.spec_from_file_location('tested_wiki_sync',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_wiki_dry_run_no_network_and_url_secret_rejection(tmp_path,monkeypatch):
    """Dry-run проверяет страницы, но не вызывает Git и не отражает credentials."""
    wiki=wiki_module();(tmp_path/'Home.md').write_text('Home')
    def forbidden(*args,**kwargs):
        """Любой git здесь был бы скрытой публикацией."""
        raise AssertionError('unexpected network')
    monkeypatch.setattr(wiki,'run_git',forbidden)
    assert wiki.sync(tmp_path,'https://example.invalid/project.wiki.git')==1
    for remote in ['file:///tmp/project.wiki.git','https://user:SECRET@host/a.wiki.git','ext::sh/a.wiki.git','https://host/a.wiki.git?token=SECRET']:
        with pytest.raises(ValueError) as error:wiki.validate_remote(remote)
        assert 'SECRET' not in str(error.value)


def test_wiki_mock_publication_preserves_foreign_pages_and_is_idempotent(tmp_path,monkeypatch):
    """Только manifest-owned устаревшая страница удаляется; неизменная публикация не commit/push."""
    wiki=wiki_module();source=tmp_path/'docs';source.mkdir();(source/'Home.md').write_text('new-home')
    remote={'.code-atlas-wiki.json':json.dumps(['Old.md']), 'Old.md':'old-managed','Foreign.md':'user content'}
    calls=[];cloned={}
    def git(args,cwd,environment):
        """Эмулирует Git поверх временного checkout без сети, сохраняя publishedfiles."""
        calls.append(args)
        assert 'SECRET' not in ' '.join(args)
        assert environment.get('WIKI_TOKEN')=='SECRET'
        if args[0]=='clone':
            checkout=Path(args[-1]);checkout.mkdir()
            for name,text in remote.items():(checkout/name).write_text(text)
            cloned.clear();cloned.update(remote)
            script=Path(environment['GIT_ASKPASS']).read_text()
            assert 'SECRET' not in script and '$WIKI_TOKEN' in script
        elif args[:3]==['diff','--cached','--name-only']:
            current={path.name:path.read_text() for path in cwd.iterdir() if path.is_file()}
            return 'changed' if current!=cloned else ''
        elif args[0]=='push':
            remote.clear();remote.update({path.name:path.read_text() for path in cwd.iterdir() if path.is_file()})
        return ''
    monkeypatch.setenv('WIKI_TOKEN','SECRET');monkeypatch.setattr(wiki,'run_git',git)
    assert wiki.sync(source,'https://example.invalid/a.wiki.git',publish=True)==1
    assert remote['Foreign.md']=='user content' and 'Old.md' not in remote
    assert remote['Home.md']=='new-home'
    calls.clear();wiki.sync(source,'https://example.invalid/a.wiki.git',publish=True)
    assert not any('commit' in call or call[0]=='push' for call in calls)


def test_wiki_rejects_manifest_symlink_before_read_or_write(tmp_path,monkeypatch):
    """Служебный manifest из удалённого репозитория не может стать write-outside ссылкой."""
    wiki=wiki_module();source=tmp_path/'docs';source.mkdir();(source/'Home.md').write_text('new-home')
    outside=tmp_path/'outside';outside.write_text('[]')
    def clone(args,cwd,environment):
        """Создаёт атакующий manifest внутри только fixture-checkout."""
        assert args[0]=='clone'
        checkout=Path(args[-1]);checkout.mkdir();(checkout/'.code-atlas-wiki.json').symlink_to(outside)
        return ''
    monkeypatch.setattr(wiki,'run_git',clone)
    with pytest.raises(ValueError):wiki.sync(source,'https://example.invalid/a.wiki.git',publish=True)
    assert outside.read_text()=='[]'


def test_wiki_refuses_overwrite_of_unmanaged_same_name_page(tmp_path,monkeypatch):
    """Первый publish не присваивает существующую чужую страницу с другим содержимым."""
    wiki=wiki_module();source=tmp_path/'docs';source.mkdir();(source/'Home.md').write_text('new')
    def clone(args,cwd,environment):
        """Создаёт чужую Home без manifest владения."""
        assert args[0]=='clone'
        checkout=Path(args[-1]);checkout.mkdir();(checkout/'Home.md').write_text('foreign')
        return ''
    monkeypatch.setattr(wiki,'run_git',clone)
    with pytest.raises(ValueError):wiki.sync(source,'https://example.invalid/a.wiki.git',publish=True)
