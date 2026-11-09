"""
Ограниченный чистый поиск class/folder предложений без изменения карты.
"""

from .contracts import ModuleSearchError
from .engine import search_partitions
from .fitness import evaluate_partition

__all__ = ['ModuleSearchError', 'search_partitions', 'evaluate_partition']
