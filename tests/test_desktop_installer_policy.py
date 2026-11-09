"""Exercise the Linux storage/identity policy on isolated directories without a GUI."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def setup_policy(monkeypatch, tmp_path):
    """Import the actual setup implementation while leaving native GUI coverage to OS runners."""
    monkeypatch.setitem(sys.modules, 'tkinter', SimpleNamespace(filedialog=None, messagebox=None))
    spec = spec_from_file_location('ag_linux_setup_test', Path(__file__).parents[1] / 'packaging/desktop/setup-linux.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'external-store'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    return module


def test_linux_repository_and_alias_paths_are_rejected(setup_policy, tmp_path):
    """The installer cannot write durable data into an indexed repository or symlink alias."""
    source = tmp_path / 'repo'
    (source / '.git').mkdir(parents=True)
    with pytest.raises(ValueError, match='repositories'):
        setup_policy.canonical(str(source / 'data'))
    (tmp_path / 'alias').symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        setup_policy.canonical(str(tmp_path / 'alias/data'))


def test_linux_external_xdg_store_prevents_silent_empty_migration(setup_policy, tmp_path):
    """A nondefault XDG store must be found before new app/data directories are created."""
    old = tmp_path / 'external-store/Example'
    old.mkdir(parents=True)
    (old / 'accepted.txt').write_text('preserve')
    product = {'appId': 'ag-design', 'defaultDataName': 'Example', 'repository': 'PonomarevRA/ag-design'}
    with pytest.raises(ValueError, match='Existing data'):
        setup_policy.install(product, [str(tmp_path / 'app'), str(tmp_path / 'empty'), str(tmp_path / 'backups')], True, False)
    assert not (tmp_path / 'app').exists()
    assert (old / 'accepted.txt').read_text() == 'preserve'


def test_linux_foreign_app_is_unchanged(setup_policy, tmp_path):
    """A user-selected unrelated nonempty destination must never be moved as an old application."""
    foreign = tmp_path / 'unrelated'
    foreign.mkdir()
    (foreign / 'important.txt').write_text('keep')
    product = {'appId': 'ag-design', 'defaultDataName': 'Example', 'repository': 'PonomarevRA/ag-design'}
    with pytest.raises(ValueError, match='identity'):
        setup_policy.install(product, [str(foreign), str(tmp_path / 'data'), str(tmp_path / 'backups')], True, False)
    assert (foreign / 'important.txt').read_text() == 'keep'
