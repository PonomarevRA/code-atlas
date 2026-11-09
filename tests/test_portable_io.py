"""Переносимые pipes Git и нативная Windows-граница чтения HANDLE."""
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from atlas.git_compare import _read_output,GitCompareError


def run_capture(code,timeout=3):
    """Запускает контролируемый дочерний Python и всегда завершает его после проверки."""
    with subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        try:
            result=_read_output(process,time.monotonic()+timeout)
            process.wait(timeout=3)
            return result
        finally:
            if process.poll() is None:
                process.kill();process.wait()


def test_portable_pipe_reader_handles_both_streams_without_deadlock():
    """Одновременные stdout/stderr читаются полностью и по отдельности."""
    result=run_capture("import os;os.write(1,b'a'*100000);os.write(2,b'b'*100000)")
    assert result['stdout']==b'a'*100000
    assert result['stderr']==b'b'*100000


@pytest.mark.parametrize('descriptor,size',[(1,4*1024*1024+1),(2,128*1024+1)])
def test_portable_pipe_reader_bounds_output(descriptor,size):
    """Ни одна труба не может бесконечно накапливать вывод в памяти."""
    with pytest.raises(GitCompareError) as error:
        run_capture(f'import os;os.write({descriptor},b"x"*{size})')
    assert error.value.status==413


def test_portable_pipe_reader_deadline():
    """Молчащий дочерний процесс ограничен deadline, а не ожиданием EOF."""
    started=time.monotonic()
    with pytest.raises(GitCompareError) as error:
        run_capture('import time;time.sleep(10)',timeout=.1)
    assert error.value.status==504
    assert time.monotonic()-started<3


@pytest.mark.skipif(os.name!='nt',reason='Requires real Windows HANDLE APIs')
def test_windows_source_handle_reads_and_bounds_file(tmp_path):
    """Проверка реально исполняется в Windows CI, без mock ctypes на macOS."""
    from atlas.windows_source import read_windows_file
    from atlas.source_viewer import SourceError
    (tmp_path/'source.cs').write_bytes(b'class Source {}')
    assert read_windows_file(tmp_path,Path('source.cs'),100)==b'class Source {}'
    with pytest.raises(SourceError) as error:
        read_windows_file(tmp_path,Path('source.cs'),3)
    assert error.value.status==413


@pytest.mark.skipif(os.name!='nt',reason='Requires real Windows junction handling')
def test_windows_source_handle_rejects_outside_junction(tmp_path):
    """FinalPath открытого HANDLE не допускает junction за корень проекта."""
    from atlas.windows_source import read_windows_file
    from atlas.source_viewer import SourceError
    root=tmp_path/'root';outside=tmp_path/'outside';root.mkdir();outside.mkdir()
    (outside/'secret.cs').write_text('secret')
    link=root/'linked'
    result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
    if result.returncode:
        pytest.skip('Junction creation not permitted on this Windows runner')
    try:
        with pytest.raises(SourceError) as error:
            read_windows_file(root,Path('linked/secret.cs'),100)
        assert error.value.status==403
    finally:
        os.rmdir(link)
