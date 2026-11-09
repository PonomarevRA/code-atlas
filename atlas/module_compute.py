"""Pure module-projection computation, independent of source stores and transports."""

from .module_analysis import assess_partition, compare_partitions


def compute_module_projection(projection, *, algorithms=('current', 'louvain', 'leiden'),
                              seed=42, resolution=1.0):
    """
    Assess one captured projection and compare the explicitly selected providers.

    Args:
        projection: Validated module_analysis projection owned by the host; never modified.
        algorithms: Nonempty sequence of permitted provider IDs; defaults to existing baselines.
        seed: Unsigned 32-bit provider seed; booleans are rejected by the provider contract.
        resolution: Finite provider resolution in (0, 10]; SArF reports its distinct effective value.
    Returns:
        Assessment and comparison dictionaries with the original source/projection identities.
        Provider timings describe this invocation, not a production benchmark.
    Raises:
        ModuleAnalysisError: Invalid partition, provider selection, seed or resolution.

    This function performs no source reads, persistence, process creation or generation selection.
    A stable host may execute it inline or send its immutable input to a read-only worker.
    """
    assessment = assess_partition(projection)
    comparison = compare_partitions(projection, algorithms=algorithms, seed=seed,
                                    resolution=resolution)
    return {'assessment': assessment, 'comparison': comparison}
