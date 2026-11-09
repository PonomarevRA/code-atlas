"""Optional existing Phpactor/PHP adapter; project autoloaders and configuration never execute."""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile

from ..semantic_lsp import LspSession
from ..semantic_lsp_providers import _definition_facts, _fingerprint, _request_sources
from ..semantic_models import SemanticAvailability, SemanticCoverage, SemanticResult
from ..semantic_process import SemanticProcessError, discover_executable, run_bounded
from ..semantic_runtime import ProbeOutcome
from ..source_viewer import SourceError, _read_regular_file
from .lsp_base import callable_targets

# Applied at CLI boot, before the language server can consult project autoloaders.
SETTINGS = {
    'composer.enable': False,
    'language_server_configuration.auto_config': False,
    'language_server.file_events': False,
    'language_server.diagnostics_on_open': False,
    'language_server.diagnostics_on_update': False,
    'language_server.diagnostics_on_save': False,
    'language_server.diagnostic_providers': [],
    'language_server_phpstan.enabled': False,
    'language_server_psalm.enabled': False,
    'language_server_php_cs_fixer.enabled': False,
    'php_code_sniffer.enabled': False,
    'indexer.enabled_watchers': [],
    'indexer.parallel_workers': 1,
    'indexer.follow_symlinks': False,
    'indexer.stub_paths': [],
    'indexer.supported_extensions': ['php'],
}
PHP_FLAGS = ('-d', 'auto_prepend_file=', '-d', 'auto_append_file=',
             '-d', 'opcache.enable_cli=0', '-d', 'opcache.preload=',
             '-d', 'disable_functions=exec,shell_exec,system,passthru,popen,proc_open,pcntl_exec')


def _environment(workspace):
    """Keep settings/cache private and ignore PHP configuration paths inherited from a project shell."""
    environment = dict(os.environ)
    for key in ('PHPRC', 'PHP_INI_SCAN_DIR', 'COMPOSER', 'COMPOSER_HOME', 'PHPACTOR_CONFIG'):
        environment.pop(key, None)
    for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_DATA_DIRS'):
        environment[name] = str(workspace / '.atlas-private')
    return environment


def _composer(root):
    """Inspect one bounded JSON manifest as data; scripts, files and plugin metadata are not evaluated."""
    payload = _read_regular_file(root, Path('composer.json'), max_bytes=256 * 1024)
    manifest = json.loads(payload)
    if not isinstance(manifest, dict):
        raise ValueError('Composer manifest must be an object')
    return root / 'composer.json'


class PhpactorProvider:
    """Reuse PHP8.2+ and an existing Phpactor PHP entry/PHAR in an isolated captured workspace.

    PHP includes, magic dispatch, Composer-generated definitions and application
    bootstrap make a captured fragment incomplete. This first adapter therefore
    publishes candidate locations only; it cannot add confirmed call graph edges.
    """
    def __init__(self, provider_id, *, engine_path=None, php_path=None):
        """User paths override automatic global discovery; a bad explicit path never falls back."""
        self.provider_id, self.engine_path, self.php_path = provider_id, engine_path, php_path

    def installations(self):
        """Inspect PATH and a small list of global installations, never repository vendor directories."""
        php = discover_executable('php', explicit=self.php_path, project_candidates=(
            Path('/opt/homebrew/bin/php'), Path('/usr/local/bin/php')))
        if self.engine_path:
            selected = Path(self.engine_path)
            engines = [selected.resolve()] if selected.is_absolute() and selected.is_file() else []
        else:
            selected = discover_executable('phpactor')
            engines = [selected] if selected else []
            for spelling in ('~/.local/bin/phpactor', '~/.composer/vendor/bin/phpactor',
                             '~/.config/composer/vendor/bin/phpactor', '/opt/homebrew/bin/phpactor', '/usr/local/bin/phpactor'):
                path = Path(spelling).expanduser()
                if path.is_file():
                    engines.append(path.resolve())
        return php, tuple(dict.fromkeys(engines))[:3]

    @staticmethod
    def _command(php, engine, workspace, *arguments):
        """Only host-selected fixed flags are accepted; no shell command or Composer invocation is used."""
        settings = {**SETTINGS, 'indexer.project_root': str(workspace),
                    'source_code_filesystem.project_root': str(workspace),
                    'indexer.index_path': str(workspace / '.atlas-private/index')}
        return [str(php), *PHP_FLAGS, str(engine), '--working-dir', str(workspace),
                '--config-extra', json.dumps(settings), *arguments]

    @staticmethod
    def _runtime_compatible(php, workspace, cancel):
        """Ask only the trusted interpreter about version/extensions; never require subject PHP files."""
        script = "echo json_encode([PHP_VERSION_ID,extension_loaded('mbstring'),extension_loaded('tokenizer'),extension_loaded('posix')]);"
        result = run_bounded([str(php), *PHP_FLAGS, '-r', script], cwd=workspace, cancel=cancel,
                             timeout=3, max_output_bytes=4096, environment=_environment(workspace))
        value = json.loads(result.stdout)
        return (result.returncode == 0 and isinstance(value, list) and len(value) == 4
                and type(value[0]) is int and value[0] >= 80200 and value[1:] == [True, True, True])

    def probe(self, context, cancel):
        """Require PHP/extensions, actual Phpactor banner and UTF16 definition handshake, without source indexing."""
        php, engines = self.installations()
        if php is None:
            return ProbeOutcome(SemanticAvailability('missing', 'php-runtime-not-installed'))
        if not engines:
            return ProbeOutcome(SemanticAvailability('missing', 'phpactor-not-installed'))
        root = Path(context.workspace_root).resolve()
        engines = tuple(engine for engine in engines if not engine.resolve().is_relative_to(root))
        if not engines:
            return ProbeOutcome(SemanticAvailability('incompatible', 'project-installed-phpactor-would-load-project-autoloader'))
        try:
            manifest = _composer(root)
            with tempfile.TemporaryDirectory(prefix='atlas-php-probe-') as directory:
                workspace = Path(directory)
                if not self._runtime_compatible(php, workspace, cancel):
                    return ProbeOutcome(SemanticAvailability('incompatible', 'php82-or-required-extensions-unavailable'))
                for engine in engines:
                    command = self._command(php, engine, workspace, '--version', '--no-ansi')
                    output = run_bounded(command, cwd=workspace, cancel=cancel, timeout=5,
                                         max_output_bytes=8192, environment=_environment(workspace))
                    banner = (output.stdout + output.stderr).decode('utf-8', errors='replace').strip()
                    if output.returncode or re.search(r'Phpactor\s+20\d{2}\.\d{2}\.\d{2}(?:\.\d+)?', banner) is None:
                        continue
                    with LspSession(self._command(php, engine, workspace, 'language-server'), workspace,
                                    cancel, settings=SETTINGS, timeout=10, environment=_environment(workspace)) as session:
                        session.initialize(workspace)
                    assets = (php, engine, manifest, Path(__file__).resolve(),
                              Path(__file__).resolve().parents[1] / 'semantic_lsp.py',
                              Path(__file__).resolve().parents[1] / 'semantic_lsp_providers.py',
                              Path(__file__).with_name('lsp_base.py'))
                    return ProbeOutcome(SemanticAvailability('available', 'candidate-only; native-workspace-not-qualified-until-analysis',
                        engine_version=banner[:256], engine_fingerprint=_fingerprint(assets, banner),
                        capabilities=('definition', 'calls')), assets)
        except (OSError, ValueError, TypeError, SourceError, SemanticProcessError):
            return ProbeOutcome(SemanticAvailability('incompatible', 'phpactor-runtime-context-or-protocol-unavailable'))
        return ProbeOutcome(SemanticAvailability('incompatible', 'phpactor-version-or-runtime-unavailable'))

    def analyze(self, context, probe, request, cancel):
        """Get source-anchored candidates without loading Composer autoload.php, configs, scripts or dynamic includes."""
        contents, queries = _request_sources(context, request)
        if any(Path(path).suffix.lower() != '.php' for path in contents):
            raise ValueError('Only captured PHP files are accepted')
        php, engine = probe.assets[:2]
        expected = _fingerprint(probe.assets, probe.availability.engine_version)
        if expected != probe.availability.engine_fingerprint:
            raise ValueError('Phpactor context changed after discovery')
        _composer(Path(context.workspace_root))
        with tempfile.TemporaryDirectory(prefix='atlas-php-') as directory:
            workspace = Path(directory)
            for path, text in contents.items():
                # Do not stage filenames the engine treats as executable project configuration.
                if Path(path).name in {'.phpactor.php', 'phpactor.php'}:
                    raise ValueError('Project configuration is not an analysis source')
                destination = workspace / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding='utf-8')
            (workspace / 'composer.json').write_text(json.dumps({'name': 'atlas/captured',
                'autoload': {'classmap': sorted(contents)}}), encoding='utf-8')
            with LspSession(self._command(php, engine, workspace, 'language-server'), workspace, cancel,
                            settings=SETTINGS, environment=_environment(workspace)) as session:
                session.initialize(workspace)
                for path, text in contents.items():
                    session.notify('textDocument/didOpen', {'textDocument': {
                        'uri': (workspace / path).as_uri(), 'languageId': 'php', 'version': 1, 'text': text}})
                facts, _, failures = _definition_facts(session, workspace, context, self.provider_id,
                    contents, queries, callable_target=callable_targets(contents))
            facts = tuple(replace(fact, confidence='candidate', evidence='Phpactor definition in captured fragment; PHP/Composer execution context is unproven.')
                          if fact.confidence == 'confirmed-static' else fact for fact in facts)
        if _fingerprint(probe.assets, probe.availability.engine_version) != expected:
            raise ValueError('Phpactor context changed during analysis')
        reasons = tuple(sorted(set((*failures, 'php-captured-fragment-candidate-only',
            'composer-autoload-and-project-bootstrap-disabled', 'dynamic-include-magic-dispatch-unknown'))))
        coverage = {'definition': SemanticCoverage('partial', 0, len(queries), reasons),
                    'calls': SemanticCoverage('partial', 0, sum(query.get('kind') == 'call' for query in queries), reasons)}
        return SemanticResult(context, self.provider_id, context.provider_fingerprint,
                              probe.availability.engine_fingerprint, 'partial', facts, coverage, reasons)
