"""Чистый class/folder анализ: проекция, оценка и сменные предложения кластеров."""

from .metrics import assess_partition
from .models import ModuleAnalysisError
from .providers import compare_partitions

__all__ = ['ModuleAnalysisError', 'project_modules', 'assess_partition', 'compare_partitions']


def __getattr__(name):
    """
    Load source projection only when its public export is requested.

    Args:
        name: Public module attribute being resolved by Python import or attribute access.
    Returns:
        The original project_modules function, preserving its signature and implementation.
    Raises:
        AttributeError: The requested name is not a lazy public export.

    Pure projection consumers such as compute workers need metrics/providers, while source
    extraction imports code-health/parser dependencies. Keeping those imports at the host
    boundary avoids loading a source-analysis runtime inside a projection-only worker.
    """
    if name == 'project_modules':
        from .projection import project_modules
        return project_modules
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
