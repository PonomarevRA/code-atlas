"""Request-local parse reuse for host-owned source; no repository or process-wide cache."""
import ast
from collections import OrderedDict
from threading import RLock
from weakref import ReferenceType, ref

MAX_RETAINED_AST_NODES = 20_000
MAX_REQUEST_AST_NODES = 100_000
MAX_REQUEST_AST_SOURCE_BYTES = 1024 * 1024


class ParseBudget:
    """Bound retained syntax across one host request, evicting the least recently used tree.

    Bytes count each tree's encoded source, not native parser allocation. Node and byte
    caps jointly bound retention; transient parsing still obeys the host's source budget.
    The host owns this budget; caches refer back weakly so dropping the host releases
    native trees immediately instead of waiting for cyclic garbage collection.
    """

    def __init__(self, max_nodes=MAX_REQUEST_AST_NODES, max_bytes=MAX_REQUEST_AST_SOURCE_BYTES):
        """Create independent counters and a reentrant lock for concurrent descriptions."""
        self.max_nodes, self.max_bytes = max_nodes, max_bytes
        self.nodes = self.bytes = 0
        self.entries = OrderedDict()
        self.lock = RLock()

    def discard(self, cache):
        """Remove a changed source's accounting before invalidating its grammar trees."""
        for key in list(self.entries):
            if key[0] == id(cache):
                self._remove(key)

    def _remove(self, key):
        """Evict one tree from its owning source and release its recorded budget."""
        cache, nodes, size = self.entries.pop(key)
        cache.get('trees', {}).pop(key[1], None)
        self.nodes -= nodes
        self.bytes -= size

    def touch(self, cache, grammar):
        """Keep frequently described files resident without retaining mutable flow results."""
        key = (id(cache), grammar)
        if key in self.entries:
            self.entries.move_to_end(key)

    def retain(self, cache, grammar, tree, nodes, size):
        """Retain one eligible tree, evicting older trees until both request caps fit."""
        if nodes > self.max_nodes or size > self.max_bytes:
            return False
        while self.entries and (self.nodes + nodes > self.max_nodes or self.bytes + size > self.max_bytes):
            self._remove(next(iter(self.entries)))
        cache.setdefault('trees', {})[grammar] = tree
        self.entries[(id(cache), grammar)] = (cache, nodes, size)
        self.nodes += nodes
        self.bytes += size
        return True


def _cache_budget(cache):
    """Resolve weak host ownership, tolerating an explicitly supplied budget for direct callers."""
    handle = cache.get('_budget') if isinstance(cache, dict) else None
    return handle() if isinstance(handle, ReferenceType) else handle


def _cache(source):
    """Use only an opt-in host cache, invalidating all syntax when exact text changes."""
    cache = source.get('_flow_parse_cache')
    if not isinstance(cache, dict):
        return None
    budget = _cache_budget(cache)
    if budget is not None and not isinstance(cache.get('_budget'), ReferenceType):
        # Entries retain their source caches for eviction, so the reverse link must
        # stay weak, including after text invalidation and direct caller setup.
        cache['_budget'] = ref(budget)
    text = source['text']
    if cache.get('text') != text:
        handle = cache.get('_budget')
        if budget is not None:
            budget.discard(cache)
        cache.clear()
        if handle is not None:
            cache['_budget'] = handle
        cache['text'] = text
    return cache


def source_bytes(source):
    """Encode one retained source once; direct adapter callers remain free of hidden state."""
    cache = source.get('_flow_parse_cache')
    budget = _cache_budget(cache)
    if budget is not None:
        with budget.lock:
            return _source_bytes(source)
    return _source_bytes(source)


def _source_bytes(source):
    """Access source-local encoded bytes under the caller's request lock when one exists."""
    cache = _cache(source)
    if cache is None:
        return source['text'].encode('utf-8')
    if 'bytes' not in cache:
        cache['bytes'] = source['text'].encode('utf-8')
    return cache['bytes']


def _retained_node_count(tree, limit):
    """Count bounded syntax, using Tree-sitter's native count without allocating child lists."""
    if not isinstance(tree, ast.AST):
        count = tree.root_node.descendant_count
        return count if count <= limit else None
    pending, count = [tree], 0
    while pending:
        current = pending.pop()
        count += 1
        if count > limit:
            return None
        pending.extend(ast.iter_child_nodes(current))
    return count


def _parse_and_retain(source, grammar, parse, max_retained_nodes):
    """Reuse immutable syntax or parse accurately when retention is unavailable or exhausted."""
    cache = _cache(source)
    if cache is None:
        return parse(source_bytes(source))
    trees = cache.setdefault('trees', {})
    budget = _cache_budget(cache)
    if grammar in trees:
        if budget is not None:
            budget.touch(cache, grammar)
        return trees[grammar]
    content = source_bytes(source)
    tree = parse(content)
    # A rejected tree is not held through another node reference. The request-local
    # negative decision avoids recounting it, but a later description must parse again.
    rejected = cache.setdefault('unretained', set())
    if grammar in rejected:
        return tree
    count = _retained_node_count(tree, max_retained_nodes) if len(content) <= MAX_REQUEST_AST_SOURCE_BYTES else None
    if count is None:
        rejected.add(grammar)
    elif budget is None:
        trees[grammar] = tree
    elif not budget.retain(cache, grammar, tree, count, len(content)):
        rejected.add(grammar)
    return tree


def parsed_tree(source, grammar, parse, *, max_retained_nodes=MAX_RETAINED_AST_NODES):
    """Reuse syntax per exact text and grammar under independent request retention caps.

    Parsers remain local to parse(). Host-owned sources share a request lock so concurrent
    descriptions cannot parse or charge the same tree twice. Above-cap syntax is parsed
    afresh without retention; this explicit CPU/memory tradeoff does not change facts.
    """
    cache = source.get('_flow_parse_cache')
    budget = _cache_budget(cache)
    if budget is None:
        return _parse_and_retain(source, grammar, parse, max_retained_nodes)
    with budget.lock:
        return _parse_and_retain(source, grammar, parse, max_retained_nodes)
