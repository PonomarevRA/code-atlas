"""Halstead inventory for Atlas's explicit local token policy, not a vendor-compatible analyzer."""

from collections import Counter
from math import log2

VERSION = 'atlas-halstead-v1'
STRINGS = {'string', 'string_literal', 'verbatim_string_literal', 'raw_string_literal',
           'interpreted_string_literal', 'template_string', 'character_literal', 'char_literal'}
LITERALS = {'true', 'false', 'null', 'none', 'nil'}


class HalsteadTokens:
    """Count tokens in the existing named AST walk; never retain source excerpts in results."""

    def __init__(self):
        """Retain at most the caller's bounded AST inventory and explicit unsupported constructs."""
        self.operators = Counter()
        self.operands = Counter()
        self.unsupported = set()
        self.atomic = set()

    def observe(self, node, source: bytes):
        """Count anonymous children once; identifiers/literals are operands and strings are atomic.

        Keywords and punctuation are operators. Deferred bodies are excluded by
        the caller. Interpolation prevents a complete token measurement.
        """
        if node.parent and node.parent.id in self.atomic:
            self.atomic.add(node.id)
            return
        kind = node.type
        if 'comment' in kind:
            return
        if kind in STRINGS:
            self.atomic.add(node.id)
            self.operands[source[node.start_byte:node.end_byte]] += 1
            if any('interpolation' in child.type or child.type == 'template_substitution'
                   for child in node.named_children):
                self.unsupported.add('string interpolation excluded')
            return
        if not node.children:
            token = source[node.start_byte:node.end_byte]
            if not token:
                return
            operand = ('identifier' in kind or 'literal' in kind or
                       kind in LITERALS or kind in {'integer', 'float', 'number'})
            (self.operands if operand else self.operators)[token] += 1
        else:
            for child in node.children:
                if not child.is_named:
                    token = source[child.start_byte:child.end_byte]
                    if token.strip():
                        self.operators[token] += 1

    def result(self, *, partial: bool, deferred_bodies: int):
        """Derive V/D/E only for complete nonempty inventories; effort has no time or defect unit."""
        n1, n2 = len(self.operators), len(self.operands)
        total_operators, total_operands = sum(self.operators.values()), sum(self.operands.values())
        vocabulary, length = n1 + n2, total_operators + total_operands
        complete = not partial and not self.unsupported
        volume = length * log2(vocabulary) if complete and vocabulary > 1 else None
        difficulty = (n1 / 2) * (total_operands / n2) if complete and n1 and n2 else None
        return {'version': VERSION, 'status': 'complete' if complete else 'partial',
                'distinctOperators': n1, 'distinctOperands': n2,
                'totalOperators': total_operators, 'totalOperands': total_operands,
                'vocabulary': vocabulary, 'length': length,
                'volume': round(volume, 3) if volume is not None else None,
                'difficulty': round(difficulty, 3) if difficulty is not None else None,
                'effort': round(volume * difficulty, 3) if volume is not None and difficulty is not None else None,
                'excludedDeferredBodies': deferred_bodies, 'unsupported': sorted(self.unsupported),
                'tokenPolicy': 'named identifiers/literals as operands; keywords/punctuation as operators; strings atomic; comments excluded',
                'scope': 'local body; deferred bodies and callees excluded',
                'interpretation': 'Descriptive token measures; not seconds, defects, Understand parity or Visual Studio Maintainability Index.'}
