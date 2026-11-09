"""Application-owned strategy factories; manifests never name commands or Python imports."""
from .semantic_models import SemanticAvailability
from .semantic_runtime import ProbeOutcome


class UnavailableProvider:
    """Fail closed when a host installation lacks the requested optional adapter."""
    def __init__(self, provider_id):
        self.provider_id = provider_id

    def probe(self, context, cancel):
        """Report host capability separately from an absent user engine."""
        return ProbeOutcome(SemanticAvailability('incompatible', 'host-adapter-unavailable'))


def create_semantic_provider(declaration, paths=None):
    """Dispatch a validated fixed strategy to host code, without repository-controlled loading."""
    identifier = declaration['modId'] + '/' + declaration['id']
    strategy = declaration['strategy']
    paths = paths or {}
    try:
        if strategy == 'typescript-local-v1':
            from .semantic_providers.typescript import TypeScriptProvider
            return TypeScriptProvider(identifier, language=declaration['language'],
                                      engine_path=paths.get('enginePath'), node_path=paths.get('nodePath'))
        if strategy == 'jedi-local-v1':
            from .semantic_providers.python import JediProvider
            return JediProvider(identifier, python_path=paths.get('pythonPath'))
        if strategy == 'roslyn-local-v1':
            from .semantic_providers.roslyn import RoslynProvider
            return RoslynProvider(identifier, dotnet=paths.get('dotnetPath'),
                                  worker_path=paths.get('workerPath'), sdk_root=paths.get('sdkPath'))
        if strategy == 'phpactor-local-v1':
            from .semantic_providers.php import PhpactorProvider
            return PhpactorProvider(identifier, engine_path=paths.get('enginePath'), php_path=paths.get('phpPath'))
        from .semantic_lsp_providers import create_lsp_provider
        return create_lsp_provider(identifier, declaration['language'], engine_path=paths.get('enginePath'))
    except ImportError:
        return UnavailableProvider(identifier)
