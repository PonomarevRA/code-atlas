"""Versioned, read-only design patterns shared by agent lookup and the wiki."""

from .catalog import PatternCatalog
from .schema import PatternCatalogError

__all__ = ["PatternCatalog", "PatternCatalogError"]
