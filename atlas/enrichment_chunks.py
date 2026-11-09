"""
Bound local file scheduling without resetting whole-snapshot fact budgets.
"""


def file_batches(files, max_files=32):
    """
    Yield ordered whole-file batches; the caller retains one shared capture.

    max_files limits scheduling granularity, not the total source allowance.
    The original order is preserved because enrichment IDs and evidence must not
    depend on how files are assigned to worker batches.
    """
    if type(max_files) is not int or not 1 <= max_files <= 1024:
        raise ValueError('Enrichment batch size must be between 1 and 1024')
    batch = []
    for filename in files:
        batch.append(filename)
        if len(batch) == max_files:
            yield batch
            batch = []
    if batch:
        yield batch
