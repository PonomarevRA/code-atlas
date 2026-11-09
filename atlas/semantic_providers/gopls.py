"""Existing gopls/Go only, offline captured-source module without cgo or package hooks."""
import re
import os
from pathlib import Path
import tempfile
from .lsp_base import CapturedLspProvider, read_context
from ..semantic_process import SemanticProcessError, discover_executable, run_bounded


class GoplsProvider(CapturedLspProvider):
    """Use static go.mod identity; external module requirements are not fetched."""
    language, binary = 'go', 'gopls'
    version_flags = ('version',)
    known_locations = ('~/go/bin/gopls', '~/.local/bin/gopls', '/opt/homebrew/bin/gopls', '/usr/local/bin/gopls')
    settings = {'buildFlags': ['-mod=readonly'], 'expandWorkspaceToModule': False, 'staticcheck': False,
                'env': {'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local',
                        'GOWORK': 'off', 'GOPACKAGESDRIVER': 'off', 'CGO_ENABLED': '0'}}

    def context_assets(self, root):
        """A real local Go toolchain and simple module declaration are required."""
        locations = [Path(path) for path in ('/usr/local/go/bin/go', '/opt/homebrew/opt/go/libexec/bin/go',
                                            '/usr/local/opt/go/libexec/bin/go')]
        if os.environ.get('GOROOT'):
            locations.insert(0, Path(os.environ['GOROOT']) / 'bin' / 'go')
        tool = discover_executable('go', project_candidates=locations)
        if tool is None:
            raise ValueError('Go toolchain is missing')
        try:
            with tempfile.TemporaryDirectory(prefix='atlas-go-version-') as directory:
                workspace = Path(directory)
                result = run_bounded([str(tool), 'version'], cwd=workspace, timeout=3,
                                     max_output_bytes=4096, environment=self.environment(workspace))
            if result.returncode or re.search(rb'go version go1\.\d+', result.stdout) is None:
                raise ValueError('Unsupported Go toolchain')
        except SemanticProcessError as error:
            raise ValueError('Go toolchain is unavailable') from error
        self._module(root)
        return root / 'go.mod', tool

    def version_supported(self, text):
        """Only actual gopls v0 banners qualify the initial LSP adapter family."""
        return re.search(r'(?:gopls|golang.org/x/tools/gopls).*v0\.\d+', text) is not None

    @staticmethod
    def _module(root):
        """Read module name and language version as data, ignoring toolchain/replace/scripts."""
        text = read_context(root / 'go.mod').decode('utf-8')
        module = re.search(r'^\s*module\s+([\w./-]+)\s*$', text, re.MULTILINE)
        version = re.search(r'^\s*go\s+(1\.\d+(?:\.\d+)?)\s*$', text, re.MULTILINE)
        if module is None or version is None:
            raise ValueError('Unsupported Go module context')
        return module[1], version[1]

    def prepare_context(self, root, workspace, contents):
        """Do not copy go.work, replacements or package-driver hooks into the private workspace."""
        module, version = self._module(root)
        (workspace / 'go.mod').write_text(f'module {module}\n\ngo {version}\n', encoding='utf-8')
        return ('external-module-dependencies-not-loaded', 'cgo-and-package-driver-disabled')
