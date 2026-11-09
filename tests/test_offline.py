"""Проверяет сетевой барьер в отдельном процессе, не меняя сокеты самого pytest."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["graphify", "atlas.graphistry_worker"])
def test_offline_runner_blocks_ip_and_dns_but_allows_local_ipc(module):
    """TCP, UDP и DNS отклоняются до сети; Unix socketpair остаётся рабочим."""
    script = '''
import socket
import sys
from atlas import offline_runner

def probe(module, **kwargs):
    operations = [
        lambda: socket.getaddrinfo("example.com", 443),
        lambda: socket.create_connection(("127.0.0.1", 1)),
        lambda: socket.socket().connect(("127.0.0.1", 1)),
        lambda: socket.socket().connect_ex(("127.0.0.1", 1)),
        lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 1)),
    ]
    for operation in operations:
        try:
            operation()
        except OSError as error:
            assert "Code Atlas" in str(error)
        else:
            raise AssertionError("Network operation was not blocked")
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ok")
        assert right.recv(2) == b"ok"
    print("offline barrier passed")

offline_runner.runpy.run_module = probe
sys.argv = ["offline_runner", sys.argv[1]]
offline_runner.main()
'''
    result = subprocess.run(
        [sys.executable, "-c", script, module],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "offline barrier passed" in result.stdout
