"""Optional rust-analyzer with static rust-project.json, no Cargo or procedural macros."""
import json
from pathlib import Path
import re

from .lsp_base import CapturedLspProvider, read_context


class RustAnalyzerProvider(CapturedLspProvider):
    """Analyze an existing exported project without running Cargo/build scripts or rustup."""
    language, binary = 'rust', 'rust-analyzer'
    known_locations = ('~/.cargo/bin/rust-analyzer', '/opt/homebrew/bin/rust-analyzer',
                       '/usr/local/bin/rust-analyzer')
    settings = {'rust-analyzer': {'cargo': {'buildScripts': {'enable': False}, 'autoreload': False},
        'procMacro': {'enable': False}, 'checkOnSave': False, 'check': {'enable': False},
        'files': {'watcher': 'client'}, 'diagnostics': {'enable': False}}}

    def version_supported(self, text):
        """Require the installed analyzer's own version banner."""
        return re.search(r'\brust-analyzer\s+(?:1\.|0\.|20\d\d-)', text) is not None

    @staticmethod
    def _project(root):
        """Accept bounded JSON exports only; paths, build outputs and proc macros are not imported."""
        value = json.loads(read_context(root / 'rust-project.json'))
        crates = value.get('crates')
        if not isinstance(crates, list) or not 1 <= len(crates) <= 16:
            raise ValueError('Missing bounded Rust crate context')
        selected = []
        for crate in crates:
            path = Path(crate['root_module'])
            path = path if path.is_absolute() else root / path
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            if crate.get('edition') not in {'2015', '2018', '2021', '2024'}:
                raise ValueError('Unsupported Rust edition')
            dependencies = crate.get('deps', [])
            if not isinstance(dependencies, list) or len(dependencies) > 32:
                raise ValueError('Rust dependency context exceeds budget')
            deps = []
            for dep in dependencies:
                if (type(dep.get('crate')) is not int or not 0 <= dep['crate'] < len(crates)
                        or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', dep.get('name', ''))):
                    raise ValueError('Invalid Rust dependency')
                deps.append({'crate': dep['crate'], 'name': dep['name']})
            selected.append({'root_module': relative, 'edition': crate['edition'], 'deps': deps})
        return selected

    def context_assets(self, root):
        """Only a pre-existing static export qualifies; Cargo.toml alone is insufficient."""
        self._project(root)
        return (root / 'rust-project.json',)

    def prepare_context(self, root, workspace, contents):
        """Rewrite exported roots to pinned files and discard proc-macro/build output metadata."""
        crates = self._project(root)
        for crate in crates:
            if crate['root_module'] not in contents:
                raise ValueError('Rust crate root not captured')
            crate['root_module'] = str(workspace / crate['root_module'])
            crate['cfg'] = []
            crate['is_workspace_member'] = True
        (workspace / 'rust-project.json').write_text(json.dumps({'sysroot': None, 'crates': crates}))
        return ('rust-sysroot-and-external-crates-not-loaded', 'build-scripts-and-proc-macros-disabled')

    def initialization_options(self):
        """Use only the exported project and prohibit Cargo-based project discovery."""
        return self.settings['rust-analyzer']

    def environment(self, workspace):
        """Disable rustup automatic installation even when a discovered binary is a shim."""
        return {**super().environment(workspace), 'RUSTUP_AUTO_INSTALL': '0'}
