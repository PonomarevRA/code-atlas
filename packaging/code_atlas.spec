# Native onedir builds keep a real executable for isolated Graphify/MCP workers.
import importlib.metadata
import json
import os
from pathlib import Path
import pkgutil
import ssl
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'packaging'))
from native_documents import DOCUMENT_PACKAGES, DOCUMENT_DISTRIBUTIONS
from build_target import select_target, lnav_pack_data
from semantic_engines import semantic_worker_data
from graphify_grammars import check_sql_grammar

build_target = select_target(os.environ.get('ATLAS_BUILD_TARGET'))
check_sql_grammar()

native_root = Path(os.environ.get('ATLAS_NATIVE_DOCUMENTS_ROOT', root / 'build' / 'native-documents'))
native_overlay = native_root / 'overlay'
native_manifest_path = native_root / 'native-documents-manifest.json'
if not native_manifest_path.is_file():
    raise RuntimeError('Run packaging/build.py first: compiled document modules are required.')
native_manifest = json.loads(native_manifest_path.read_text(encoding='utf-8'))
sys.path.insert(0, str(native_overlay))
hiddenimports = collect_submodules('atlas') + collect_submodules('desktop_support')
hiddenimports += collect_submodules('pygments.lexers')
hiddenimports += ['webview', 'webview.menu']
desktop_backend = {'darwin': 'cocoa', 'win32': 'winforms'}.get(sys.platform, 'qt')
hiddenimports += ['webview.platforms.' + desktop_backend]
for package in ['html5lib', 'cssselect2', 'tinycss2', 'webencodings']:
    hiddenimports += collect_submodules(package)
datas = [(str(root / 'web'), 'web'), (str(root / 'skills'), 'skills'),
         (str(root / 'packaging' / 'assets'), 'packaging/assets')]
if (root / 'PUBLIC_SOURCE_MANIFEST.json').is_file():
    datas.append((str(root / 'PUBLIC_SOURCE_MANIFEST.json'), '.'))
from browser_privacy_pack import privacy_pack_data
datas += privacy_pack_data(root)
roslyn_directory = os.environ.get('ATLAS_ROSLYN_WORKER_ROOT')
datas += semantic_worker_data(root, Path(roslyn_directory) if roslyn_directory else None)
# Preserve the build runtime's certificate policy beside frozen ssl.py; never relax TLS verification.
ssl_policy = Path(ssl.__file__).parent / 'codex-rejected-cas.pem'
if ssl_policy.is_file():
    datas.append((str(ssl_policy), '.'))
if (root / 'docs' / 'wiki').is_dir():
    datas.append((str(root / 'docs' / 'wiki'), 'docs/wiki'))
if (root / 'resources' / 'language-references').is_dir():
    datas.append((str(root / 'resources' / 'language-references'), 'resources/language-references'))
if (root / 'resources' / 'reference-search').is_dir():
    datas.append((str(root / 'resources' / 'reference-search'), 'resources/reference-search'))
datas.append((str(root / "resources" / "code-health"), "resources/code-health"))
datas.append((str(root / "resources" / "code-patterns"), "resources/code-patterns"))
datas += lnav_pack_data(root, build_target)
datas += [(str(root / "resources" / "integrations"), "resources/integrations"), (str(root / "mods"), "mods")]
binaries = []
# Cython extensions contain imports invisible to bytecode analysis. Explicitly
# retain parser modules, metadata and native dependencies for offline use.
hiddenimports += native_manifest['hiddenImports']
for extension in native_manifest['extensions']:
    binary_path = native_overlay / extension['file']
    if not binary_path.is_file():
        raise RuntimeError('Missing required compiled document module: ' + str(binary_path))
    binaries.append((str(binary_path), str(Path(extension['file']).parent)))
datas.append((str(native_manifest_path), 'resources'))
for package in DOCUMENT_PACKAGES:
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in DOCUMENT_DISTRIBUTIONS:
    datas += copy_metadata(distribution, recursive=True)
for package in ['graphify', 'graphistry', 'igraph', 'lancedb', 'fastembed', 'onnxruntime', 'tokenizers', 'ijson', 'gherkin']:
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
# Research graphs import the engine lazily; retain namespace modules and native serializers offline.
for package in ['langgraph', 'langchain_core', 'langsmith', 'sqlite_vec', 'ormsgpack', 'orjson', 'xxhash', 'uuid_utils']:
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ['langgraph', 'langgraph-checkpoint-sqlite']:
    datas += copy_metadata(distribution, recursive=True)
for distribution in ['html5lib', 'cssselect2', 'tinycss2', 'webencodings']:
    datas += copy_metadata(distribution)
# Graphify selects these grammar modules dynamically by file extension.
for module in pkgutil.iter_modules():
    if module.name.startswith('tree_sitter_'):
        package_datas, package_binaries, package_imports = collect_all(module.name)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_imports
for distribution in ['graphifyy', 'tree-sitter-sql', 'mcp', 'fastapi', 'uvicorn', 'httpx', 'graphistry', 'igraph', 'pillow', 'lancedb', 'fastembed', 'onnxruntime', 'tokenizers', 'ijson', 'gherkin-official', 'pywebview']:
    datas += copy_metadata(distribution)
hiddenimports += ['PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'uvicorn.logging',
                  'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto',
                  'uvicorn.lifespan.on', 'atlas.offline_runner', 'atlas.agent_mcp', 'atlas.snapshot_png_worker']
analysis = Analysis([str(root / 'packaging' / 'entrypoint.py')], pathex=[str(native_overlay), str(root)],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    excludes=['pytest', 'IPython', 'jupyter', 'notebook', 'tkinter', 'torch', 'transformers'], noarchive=False)
# Never silently fall back to Python source for modules promised as native.
compiled_modules = {entry['module'] for entry in native_manifest['extensions']}
analysis.pure = [entry for entry in analysis.pure if entry[0] not in compiled_modules]
archive = PYZ(analysis.pure)
icon = root / 'packaging' / 'assets' / ('code-atlas.icns' if sys.platform == 'darwin' else 'code-atlas.ico')
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name='CodeAtlas',
                 console=True, icon=str(icon) if icon.is_file() else None,
                 target_arch=None, codesign_identity=None, entitlements_file=None)
executables = [executable]
if sys.platform == 'win32':
    # GUI ярлык не создаёт консоль; отдельный console executable сохраняет MCP stdio.
    executables.append(EXE(archive, analysis.scripts, [], exclude_binaries=True,
                           name='CodeAtlasDesktop', console=False,
                           icon=str(icon) if icon.is_file() else None))
collection = COLLECT(*executables, analysis.binaries, analysis.datas, name='CodeAtlas')
if sys.platform == 'darwin':
    bundle = BUNDLE(collection, name='Code Atlas.app', icon=str(icon) if icon.is_file() else None,
                    bundle_identifier='local.code-atlas.desktop', version='0.1.7',
                    info_plist={'CFBundleDisplayName': 'Code Atlas', 'NSHighResolutionCapable': True,
                                'NSDocumentsFolderUsageDescription': 'Code Atlas читает выбранные вами локальные репозитории для построения карт и просмотра исходников.'})
