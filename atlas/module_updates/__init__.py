"""Opt-in Atlas incremental directed pilot; not an LD-Leiden implementation."""

from .engine import derive_delta, initial_state, update
from .model import ModuleUpdateError, UpdateResult, UpdateState

__all__ = ['ModuleUpdateError', 'UpdateResult', 'UpdateState', 'derive_delta', 'initial_state', 'update']
