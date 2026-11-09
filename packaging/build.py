"""Воспроизводимый native build; запускать отдельно на каждом целевом OS/architecture."""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from native_documents import build_overlay
from build_target import select_target
from native_resources import restore_native_resources
from browser_privacy_pack import restore_privacy_pack, restore_human_auth_pack
from semantic_engines import prepare_roslyn_worker, semantic_worker_data, semantic_worker_manifest
from graphify_grammars import check_sql_grammar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    """
    Build onedir/app without package installation, checking required offline SQL support first.

    Returns:
        None after publishing the native artifact and its platform/dependency manifest.
    Raises:
        RuntimeError: Pinned SQL grammar is unavailable or incompatible with the build interpreter.
        SystemExit: Target selection or required static resources are invalid.
        subprocess.CalledProcessError: Native compilation or packaging fails.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--clean', action='store_true',
                        help='Compatibility flag: release builds always clean PyInstaller caches')
    parser.add_argument('--target', default=os.environ.get('ATLAS_BUILD_TARGET'),
                        help='Native OS-architecture; must match the current Python process')
    parser.add_argument('--roslyn-worker-root', type=Path,
                        help='Existing two-file Atlas bridge; otherwise build offline with installed SDK8 when available')
    args = parser.parse_args()
    try:
        target = select_target(args.target)
    except ValueError as error:
        parser.error(str(error))
    sql_grammar = check_sql_grammar()
    font = ROOT / 'packaging/assets/NotoSans-Regular.ttf'
    license_path = ROOT / 'packaging/assets/NotoSans-LICENSE.txt'
    if not font.is_file() or not license_path.is_file():
        raise SystemExit('Положите NotoSans-Regular.ttf и его OFL license в packaging/assets до сборки.')
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--distpath', str(ROOT / 'dist'),
               '--workpath', str(ROOT / 'build'), str(ROOT / 'packaging/code_atlas.spec')]
    # Overlay snapshots preserve source mtimes. A cached PYZ could otherwise
    # predate source content despite a fresh Analysis; always discard freeze caches.
    environment = dict(os.environ)
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    command_line_tools = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and command_line_tools.is_dir() and not environment.get("DEVELOPER_DIR"):
        # Уже установленный самостоятельный CLT не требует переключать Xcode
        # глобально или принимать лицензию другого выбранного toolchain.
        environment["DEVELOPER_DIR"] = str(command_line_tools)
        environment["PATH"] = str(command_line_tools / "usr/bin") + os.pathsep + environment.get("PATH", "")
    native_root = ROOT / 'build' / 'native-documents'
    native_manifest = build_overlay(ROOT, native_root, environment=environment)
    environment['ATLAS_NATIVE_DOCUMENTS_ROOT'] = str(native_root)
    environment['ATLAS_BUILD_TARGET'] = target
    roslyn = prepare_roslyn_worker(ROOT, prebuilt=args.roslyn_worker_root)
    environment['ATLAS_ROSLYN_WORKER_ROOT'] = roslyn['directory'] or ''
    semantic_resources = semantic_worker_manifest(semantic_worker_data(ROOT, roslyn['directory']))
    subprocess.run(command, cwd=ROOT, check=True, env=environment)
    restore_privacy_pack(ROOT)
    restore_human_auth_pack(ROOT)
    native_resources = restore_native_resources(ROOT, target)
    installer = {'darwin': 'install-macos.sh', 'win32': 'install-windows.ps1'}.get(sys.platform, 'install-linux.sh')
    shutil.copy2(ROOT / 'packaging' / installer, ROOT / 'dist' / installer)
    manifest = {'version': '0.1.6', 'platform': sys.platform, 'architecture': target.split('-', 1)[1],
                'processMachine': platform.machine(),
                'python': platform.python_version(), 'format': 'onedir', 'dataSchemaVersion': 1,
                'target': target, 'nativeResources': native_resources, 'targetPythonRequired': False, 'nativeDocuments': native_manifest,
                'semanticWorkers': semantic_resources, 'roslynWorkerBuild': roslyn['source'],
                'requiredGrammars': {'sql': sql_grammar},
                'desktop': {'shell': 'pywebview', 'renderer': {'darwin': 'cocoa', 'win32': 'edgechromium'}.get(sys.platform, 'qt')}}
    (ROOT / 'dist/build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
