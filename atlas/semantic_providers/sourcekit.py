"""Optional SourceKit-LSP over captured compilation data; no SwiftPM/BSP builds."""
import json
from pathlib import Path
import re

from .lsp_base import CapturedLspProvider
from ..semantic_lsp_providers import _compile_entries


class SourceKitProvider(CapturedLspProvider):
    """Require an existing explicit SDK/compilation database and a recognized Swift 6 engine."""
    language, binary = 'swift', 'sourcekit-lsp'
    known_locations = ('/usr/bin/sourcekit-lsp', '/opt/homebrew/bin/sourcekit-lsp',
                       '/usr/local/bin/sourcekit-lsp')
    settings = {'backgroundIndexing': False, 'defaultWorkspaceType': 'compilationDatabase',
                'compilationDatabase': {'searchPaths': ['.']}}

    def version_supported(self, text):
        """Unknown/older versions stay incompatible because configuration keys may be ignored."""
        return re.search(r'(?i)sourcekit[- ]lsp.*(?:swift[- ]?6\.|\b6\d\d\.)', text) is not None

    @staticmethod
    def _metadata(root):
        """Read one bounded static build context, accepting no plugin/driver/response-file flags."""
        entries = _compile_entries(root / 'compile_commands.json', root)
        selected, sdk = {}, None
        for path, alternatives in entries.items():
            if not path.endswith('.swift'):
                continue
            if len(alternatives) != 1:
                raise ValueError('Ambiguous Swift compilation target')
            directory, arguments = alternatives[0]
            flags, index = [], 1
            while index < len(arguments):
                flag = arguments[index]
                if flag in {'-sdk', '-target', '-module-name', '-swift-version'}:
                    if index + 1 >= len(arguments):
                        raise ValueError('Missing Swift flag value')
                    value = arguments[index + 1]
                    if flag == '-sdk':
                        location = Path(value)
                        location = location if location.is_absolute() else directory / location
                        location = location.resolve()
                        if not (location / 'SDKSettings.json').is_file() or (sdk and sdk != location):
                            raise ValueError('Missing or inconsistent Swift SDK')
                        sdk = location
                        value = str(location)
                    elif not re.fullmatch(r'[\w.+-]{1,128}', value):
                        raise ValueError('Unsupported Swift flag value')
                    flags.extend([flag, value])
                    index += 2
                    continue
                if flag in {'-c', '-parse-as-library'}:
                    if flag == '-parse-as-library':
                        flags.append(flag)
                elif flag == '-o':
                    index += 2
                    continue
                elif not flag.startswith(('-', '@')) and Path(flag).suffix == '.swift':
                    pass
                else:
                    raise ValueError('Unsupported or executable Swift flag')
                index += 1
            selected[path] = flags
        if not selected or sdk is None:
            raise ValueError('Swift compilation database/SDK is missing')
        return selected, sdk

    def context_assets(self, root):
        """A real SDK metadata file avoids invoking xcrun or accepting an Xcode license."""
        _, sdk = self._metadata(root)
        return root / 'compile_commands.json', sdk / 'SDKSettings.json'

    def prepare_context(self, root, workspace, contents):
        """Write safe compiler arguments as data and force non-building workspace selection."""
        entries, sdk = self._metadata(root)
        sources = [str(workspace / path) for path in contents if path.endswith('.swift')]
        compilation = []
        for path in contents:
            if path in entries:
                compilation.append({'directory': str(workspace), 'file': str(workspace / path),
                    'arguments': ['swiftc', *entries[path], '-module-cache-path', str(workspace / '.module-cache'),
                                  *sources]})
        if not compilation:
            raise ValueError('Swift compilation target not captured')
        (workspace / 'compile_commands.json').write_text(json.dumps(compilation))
        options = {**self.settings, 'fallbackBuildSystem': {'sdk': str(sdk)},
                   'generatedFilesPath': str(workspace / '.generated')}
        configuration = workspace / '.sourcekit-lsp'
        configuration.mkdir()
        (configuration / 'config.json').write_text(json.dumps(options))
        return ('swift-external-project-modules-not-loaded', 'swiftpm-bsp-and-background-builds-disabled')
