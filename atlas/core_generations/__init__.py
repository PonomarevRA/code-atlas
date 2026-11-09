"""Opt-in source-only lifecycle for read-only module-projection workers."""

from .artifacts import BundleArtifact, GenerationError, build_bundle
from .coordinator import CoreGenerations

__all__ = ['BundleArtifact', 'GenerationError', 'build_bundle', 'CoreGenerations']
