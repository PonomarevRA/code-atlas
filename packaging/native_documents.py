"""Build document extensions in an isolated overlay for standalone installers.

This is build-host code. The installed application never needs Cython, a C
compiler or a separately installed Python interpreter.
"""

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

from build_target import process_target

DOCUMENT_PACKAGES = (
    'markdown_it', 'mdurl', 'docx', 'openpyxl', 'pptx', 'pypdf', 'pdfplumber',
    'pdfminer', 'pypdfium2', 'lxml', 'defusedxml', 'drain3', 'psutil',
    'et_xmlfile', 'xlsxwriter', 'cachetools', 'jsonpickle', 'cryptography',
    'charset_normalizer', 'cffi', 'PIL', 'yaml',
)
DOCUMENT_DISTRIBUTIONS = (
    'markdown-it-py', 'mdurl', 'python-docx', 'openpyxl', 'python-pptx', 'pypdf',
    'pdfplumber', 'pdfminer.six', 'pypdfium2', 'lxml', 'defusedxml', 'drain3',
    'psutil', 'et_xmlfile', 'xlsxwriter', 'cachetools', 'jsonpickle', 'cryptography',
    'charset-normalizer', 'cffi', 'pycparser', 'pillow', 'PyYAML',
)
DIRECTIVES = {'language_level': 3, 'annotation_typing': False, 'binding': True,
              'boundscheck': True, 'wraparound': True, 'initializedcheck': True}


def target_metadata() -> dict:
    """Record the interpreter ABI separately from a possibly ARM host machine label."""
    target = process_target()
    system, architecture = target.split('-', 1)
    return {'target': target, 'platform': system, 'architecture': architecture,
            'processMachine': platform.machine()}


def source_modules(root: Path) -> list[Path]:
    """Select document modules, retaining the Python package initializer."""
    return sorted(path for path in (root / 'atlas' / 'documents').glob('*.py')
                  if path.name != '__init__.py')


def imported_modules(paths: list[Path]) -> list[str]:
    """Retain imports hidden inside native modules from PyInstaller analysis."""
    imports = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != '__future__':
                name = '.' * node.level + (node.module or '')
                if node.level:
                    name = importlib.util.resolve_name(name, 'atlas.documents')
                if name:
                    imports.add(name)
    return sorted(imports)


def sha256(path: Path) -> str:
    """Hash the exact source or extension bytes recorded in the manifest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_flags() -> list[str]:
    """Use portable release optimization without changing numeric semantics."""
    return ['/O2'] if os.name == 'nt' else ['-O2']


def build_environment(environment=None) -> dict:
    """Select installed CLT locally, including Rosetta host target architecture."""
    result = dict(os.environ if environment is None else environment)
    tools = Path('/Library/Developer/CommandLineTools')
    if (sys.platform == 'darwin' and tools.is_dir()
            and result.get('DEVELOPER_DIR', str(tools)) == str(tools)):
        result.setdefault('DEVELOPER_DIR', str(tools))
        # The /usr/bin xcrun shim may be Intel while recent CLT is arm64-only.
        # Calling clang directly also makes the extension match this Python ABI.
        compiler = tools / 'usr/bin/clang'
        if compiler.is_file():
            result.setdefault('CC', str(compiler) + ' -arch ' + platform.machine())
            result.setdefault('SDKROOT', str(tools / 'SDKs/MacOSX.sdk'))
    return result


def build_overlay(root: Path, work_root: Path, *, environment=None) -> dict:
    """Compile into work_root only and return the manifest of actual outputs.

    A complete atlas source mirror keeps ordinary modules discoverable while
    document .py files are removed after successful compilation. A failed build
    cannot accidentally package a Python fallback for a document extension.
    """
    root = root.resolve()
    work_root = work_root.resolve()
    if work_root == root or root.is_relative_to(work_root) or work_root.is_relative_to(root / 'atlas'):
        raise ValueError('The build directory must not contain the source tree.')
    overlay = work_root / 'overlay'
    if overlay.exists():
        shutil.rmtree(overlay)
    overlay.mkdir(parents=True)
    shutil.copytree(root / 'atlas', overlay / 'atlas',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.so', '*.pyd'))
    sources = source_modules(root)
    if not sources:
        raise ValueError('No document sources were found.')
    source_hashes = {path.relative_to(root).as_posix(): sha256(overlay / path.relative_to(root)) for path in sources}
    hidden_imports = imported_modules([overlay / path.relative_to(root) for path in sources])
    configuration = {'overlay': str(overlay), 'sources': [str(path.relative_to(root)) for path in sources],
                     'directives': DIRECTIVES, 'compileFlags': compile_flags()}
    config_path = work_root / 'compile.json'
    config_path.write_text(json.dumps(configuration), encoding='utf-8')
    command = [sys.executable, str(Path(__file__).resolve()), '--compile-config', str(config_path)]
    environment = build_environment(environment)
    subprocess.run(command, cwd=overlay, check=True, env=environment)
    extensions = []
    suffix = sysconfig.get_config_var('EXT_SUFFIX')
    for source in sources:
        relative_source = source.relative_to(root)
        binary = (overlay / relative_source).with_suffix(suffix)
        if not binary.is_file():
            raise RuntimeError(f'Native extension was not produced: {binary}')
        extensions.append({'module': '.'.join(relative_source.with_suffix('').parts),
                           'file': binary.relative_to(overlay).as_posix(),
                           'sha256': sha256(binary), 'sourceSha256': source_hashes[relative_source.as_posix()]})
        (overlay / relative_source).unlink()
    compiler_record = json.loads((work_root / 'compiler.json').read_text(encoding='utf-8'))
    manifest = {'schemaVersion': 1, **target_metadata(),
                'python': platform.python_version(), 'pythonAbi': sys.implementation.cache_tag,
                'deploymentTarget': sysconfig.get_config_var('MACOSX_DEPLOYMENT_TARGET'),
                'hashStage': 'compiled-before-freeze-and-signing',
                'extensionSuffix': suffix, 'pythonCompiler': platform.python_compiler(),
                'compiler': compiler_record,
                'compileFlags': compile_flags(), 'directives': DIRECTIVES,
                'hiddenImports': hidden_imports, 'extensions': extensions}
    (work_root / 'native-documents-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def compile_config(path: Path) -> None:
    """Run Cython/setuptools in a separate build process with explicit flags."""
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension

    configuration = json.loads(path.read_text(encoding='utf-8'))
    overlay = Path(configuration['overlay'])
    extensions = []
    for relative in configuration['sources']:
        name = '.'.join(Path(relative).with_suffix('').parts)
        extensions.append(Extension(name, [str(overlay / relative)],
                                    extra_compile_args=configuration['compileFlags']))
    modules = cythonize(extensions, compiler_directives=configuration['directives'],
                        build_dir=str(path.parent / 'generated'), quiet=True)
    distribution = Distribution({'name': 'atlas-native-documents', 'ext_modules': modules})
    distribution.script_name = str(Path(__file__).resolve())
    command = distribution.get_command_obj('build_ext')
    command.build_lib = str(overlay)
    command.build_temp = str(path.parent / 'objects')
    command.ensure_finalized()
    command.run()
    import Cython
    compiler = command.compiler
    executable = getattr(compiler, 'compiler', None) or getattr(compiler, 'cc', None)
    if isinstance(executable, str):
        executable = [executable]
    version = ''
    if executable:
        flag = '/Bv' if compiler.compiler_type == 'msvc' else '--version'
        result = subprocess.run([executable[0], flag], capture_output=True, text=True,
                                errors='replace', check=False)
        version = (result.stdout + result.stderr)[:4000]
    record = {'type': compiler.compiler_type, 'command': executable, 'version': version,
              'cythonVersion': Cython.__version__,
              'environment': {key: os.environ[key] for key in ('CC', 'CFLAGS', 'LDFLAGS', 'SDKROOT', 'DEVELOPER_DIR') if key in os.environ}}
    (path.parent / 'compiler.json').write_text(json.dumps(record), encoding='utf-8')


def main() -> None:
    """Support the build entrypoint and its isolated compiler subprocess."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--compile-config', type=Path)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--work-root', type=Path)
    args = parser.parse_args()
    if args.compile_config:
        compile_config(args.compile_config)
    else:
        work_root = args.work_root or args.source_root / 'build' / 'native-documents'
        print(json.dumps(build_overlay(args.source_root, work_root), indent=2))


if __name__ == '__main__':
    main()
