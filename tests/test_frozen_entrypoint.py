"""Frozen executable не запускает произвольные модули и не рекурсирует в сервер."""
import importlib.util
from pathlib import Path
import sys
import pytest


def entry_module():
    """Загружает entrypoint без запуска процесса приложения."""
    spec=importlib.util.spec_from_file_location('atlas_packaged_entry',Path(__file__).parent.parent/'packaging/entrypoint.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('module',['atlas.offline_runner','atlas.agent_mcp','atlas.architecture_cli'])
def test_frozen_whitelisted_module_dispatch(module,monkeypatch):
    """Дочерний -m переключает внутренний модуль и сохраняет его argv."""
    entry=entry_module();calls=[]
    monkeypatch.setattr(entry.multiprocessing,'freeze_support',lambda:None)
    monkeypatch.setattr(entry.runpy,'run_module',lambda name,**kwargs:calls.append((name,list(sys.argv),kwargs)))
    monkeypatch.setattr(sys,'argv',['CodeAtlas','-m',module,'--port','8877'])
    entry.main()
    assert calls==[(module,[module,'--port','8877'],{'run_name':'__main__','alter_sys':True})]


def test_frozen_rejects_arbitrary_module_and_maps_png_to_bundled_module(monkeypatch):
    """Нельзя использовать runtime как произвольный Python или выполнить путь злоумышленника."""
    entry=entry_module();calls=[]
    monkeypatch.setattr(entry.multiprocessing,'freeze_support',lambda:None)
    monkeypatch.setattr(entry.runpy,'run_module',lambda name,**kwargs:calls.append(name))
    monkeypatch.setattr(sys,'argv',['CodeAtlas','-m','os','payload'])
    with pytest.raises(SystemExit):entry.main()
    assert calls==[]
    monkeypatch.setattr(sys,'argv',['CodeAtlas','/outside/snapshot_png_worker.py','in','out','font'])
    entry.main()
    assert calls==['atlas.snapshot_png_worker']
    assert sys.argv==['atlas.snapshot_png_worker','in','out','font']
