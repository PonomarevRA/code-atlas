"""macOS installer заменяет только приложение после успешной проверки и preflight."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
from native_installer_fixture import prepare_installer


@pytest.mark.skipif(sys.platform!='darwin',reason='Exercises native macOS ditto installer')
def test_macos_update_preserves_data_and_old_app_and_aborts_failed_preflight(tmp_path):
    """Проверяет реальный shellinstaller на временном mock .app, без установки в HOME."""
    source=tmp_path/'new.app';binary=source/'Contents/MacOS/CodeAtlas';binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\ncase "$0" in *.app/Contents/MacOS/CodeAtlas) exit 0;; *) exit 23;; esac\n');binary.chmod(0o755)
    destination=tmp_path/'installed.app';(destination/'Contents').mkdir(parents=True);(destination/'Contents/old').write_text('old-app')
    data=tmp_path/'data';data.mkdir();(data/'knowledge.md').write_text('keep-knowledge')
    script=Path(__file__).parent.parent/'packaging/install-macos.sh'
    script, signing_environment = prepare_installer(tmp_path, script, [source, destination], retain=destination)
    environment={**os.environ, **signing_environment,'ATLAS_DATA_DIR':str(data)}
    result=subprocess.run(['sh',str(script),str(source),str(destination)],env=environment,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert (destination/'Contents/MacOS/CodeAtlas').is_file()
    backup=next(tmp_path.glob('installed.previous-*.app'))
    assert backup.suffix=='.app'
    assert (backup/'Contents/old').read_text()=='old-app'
    assert (data/'knowledge.md').read_text()=='keep-knowledge'
    before=(destination/'Contents/MacOS/CodeAtlas').read_bytes()
    binary.write_text('#!/bin/sh\ncase "$*" in *--prepare-update*) exit 1;; esac\nexit 0\n')
    failed=subprocess.run(['sh',str(script),str(source),str(destination)],env=environment,capture_output=True,text=True,timeout=10)
    assert failed.returncode!=0
    assert (destination/'Contents/MacOS/CodeAtlas').read_bytes()==before
    assert (data/'knowledge.md').read_text()=='keep-knowledge'
    assert not list(tmp_path.glob('installed.install-*'))
