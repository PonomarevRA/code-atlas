"""Provider factory separates language parsing from the common description and map contract."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from .registry import MODULES, language_adapter
from .contract import normalize_description


class FlowProvider(Protocol):
    """Every provider returns the same bounded static description, independent of its parser."""
    id: str

    def describe(self, analysis, node: dict, base: dict) -> dict:
        """Use host-owned source access and produce the common diagram representation."""
        ...


@dataclass(frozen=True)
class ModuleProvider:
    """Adapt each independent language module to the host provider interface."""
    id: str
    analyze: Callable

    def describe(self, analysis, node: dict, base: dict) -> dict:
        """Share source budgets/provenance; parsing never receives a project filesystem handle."""
        from .host import read_and_describe
        result = read_and_describe(analysis, node, base, self.analyze)
        return normalize_description(result, self.id, analysis.max_events)


@dataclass(frozen=True)
class CsharpProvider:
    """Keep the established C# analyzer behind the same factory and output contract."""
    id: str = 'csharp'

    def describe(self, analysis, node: dict, base: dict) -> dict:
        """Reuse C# AST/source caches without duplicating parser or control-flow rules."""
        base['analyzerVersion'] = 'csharp-structure-v8-constructor-bodies'
        result = analysis._describe_csharp(node, base)
        return normalize_description(result, self.id, analysis.max_events)


def create_provider(filename, *, adapter_loader=language_adapter) -> FlowProvider | None:
    """Select only explicit built-in providers; repository data cannot name import modules."""
    suffix = Path(str(filename)).suffix.lower()
    if suffix == '.cs':
        return CsharpProvider()
    language = MODULES.get(suffix)
    if language is None:
        return None
    adapter = adapter_loader(filename)
    return ModuleProvider(language, adapter) if adapter is not None else None
