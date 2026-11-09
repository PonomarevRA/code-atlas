"""Query-independent bounded indexes for captured lexical facts, never source bodies."""
import sys
from .analysis_facts import searchable_fact_strings

MAX_FACT_INDEX_BYTES = 16 * 1024 * 1024


def build_fact_index(graph, budget=MAX_FACT_INDEX_BYTES):
    """Compile immutable rows once; deduplicate identical rows and fall back if accounted storage exceeds budget.

    graph supplies immutable captured nodes; budget bounds the accounted bytes.
    Return rows aligned with those nodes, or None to request exact uncached search.
    Each folded string is encoded with surrogatepass and separated by byte 0xff,
    which cannot occur in UTF-8. A substring cannot cross two captured values.
    Accounting includes Python byte-row sizes and conservative dictionary entry
    overhead, not a promise about allocator RSS. Oversized indexes are discarded;
    callers retain the original traversal semantics without a partial index.
    """
    rows, pool = [], {}
    used = sys.getsizeof(pool)
    for node in graph.get('nodes', []):
        row = b"\xff".join(text.encode("utf-8", "surrogatepass")
                            for text in dict.fromkeys(searchable_fact_strings(node)))
        shared = pool.get(row)
        if shared is None:
            used += sys.getsizeof(row) + 128
            pool[row] = row
            shared = row
        rows.append(shared)
        if used + sys.getsizeof(rows) + len(rows) * 8 > budget:
            return None
    return tuple(rows)
