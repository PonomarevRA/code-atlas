"""Service verification must stay silent unless the operator explicitly asks for a browser."""
import sys
from types import SimpleNamespace
import pytest
from atlas import cli


@pytest.mark.parametrize('arguments,expected', [([], 0), (['--no-open'], 0), (['--open'], 1)])
def test_cli_opens_only_on_explicit_request(monkeypatch, tmp_path, arguments, expected):
    """Run the actual CLI selection with an isolated store and a synthetic server, never a browser."""
    openings = []
    class Server:
        """No socket or renderer is created by this synthetic ready server."""
        def __init__(self, config):
            self.started = True
            self.should_exit = False
        def run(self):
            """The startup selector is the behavior under test."""
            pass
    class Thread:
        """Record only the browser-ready worker requested by the real CLI."""
        def __init__(self, **kwargs):
            openings.append(kwargs['target'])
        def start(self):
            """Never run the target or perform a browser launch."""
            pass
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(Server=Server, Config=lambda *a, **kw: None))
    monkeypatch.setattr(cli.threading, 'Thread', Thread)
    monkeypatch.setattr(sys, 'argv', ['atlas', '--data-dir', str(tmp_path / 'data'), *arguments])
    cli.main()
    assert len(openings) == expected
