"""Explicit language routing never imports code from an analyzed repository or user mod."""
from importlib import import_module
from pathlib import Path

MODULES = {
    '.py': 'python', '.java': 'java', '.js': 'javascript', '.mjs': 'javascript',
    '.cjs': 'javascript', '.jsx': 'javascript', '.ts': 'typescript', '.tsx': 'typescript',
    '.go': 'go', '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp',
    '.kt': 'kotlin', '.php': 'php', '.rs': 'rust', '.swift': 'swift',
}


def language_adapter(filename):
    """Return an installed Atlas adapter; unfinished languages keep the explicit existing fallback."""
    language = MODULES.get(Path(str(filename)).suffix.lower())
    if not language:
        return None
    module_name = __package__ + '.' + language
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return None
        raise
    return module.analyze
