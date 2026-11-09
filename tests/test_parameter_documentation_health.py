"""Check parameter documentation evidence and fixed scoring caps with real language grammars."""
from copy import deepcopy

import pytest

from atlas.code_health import calculate_health, compact_health, enrich_code_health
from atlas.code_health_comparison import compare_assessments
from atlas.code_health_policy import policy
from atlas.code_health_source import parse_health_source


def measured(filename, source):
    """Parse actual declarations and score a synthetic no-call graph without any user data."""
    methods = parse_health_source(filename, source.encode())["methods"]
    return enrich_code_health({"nodes": methods, "edges": [], "metadata": {}})["nodes"]


def csharp(parameters=1, *, purpose=False, inputs=False, result=False, text="Input amount."):
    """Keep executable structure fixed while varying only documentation and parameter count."""
    lines = []
    if purpose: lines.append('/// <summary>Calculate a doubled amount.</summary>')
    if inputs:
        lines.extend(f'/// <param name="p{i}">{text}</param>' for i in range(parameters))
    if result: lines.append('/// <returns>Doubled amount.</returns>')
    names = ', '.join('int p'+str(i) for i in range(parameters))
    return 'class A {\n'+'\n'.join(lines)+f'\nint Double({names}) {{ return '+('p0 * 2' if parameters else '42')+'; }\n}'


@pytest.mark.parametrize('count', [1, 5, 40])
def test_parameter_bonus_is_fixed_and_small_even_without_other_contract_groups(count):
    """Parameters alone add exactly 10 D points and at most 0.5 H/T points at every supported count."""
    before = measured('a.cs', csharp(count))[0]
    after = measured('a.cs', csharp(count, inputs=True))[0]
    assert after['documentationHealth']['score'] == 10
    coverage = after['documentationHealth']['parameterCoverage']
    assert coverage == {'status': 'complete', 'required': count, 'covered': count, 'coveragePercent': 100.0,
                        'missing': [], 'maximumDocumentationPoints': 10.0}
    for name in ('score', 'transparencyScore'):
        assert after['health'][name] - before['health'][name] == pytest.approx(0.5)
    # No result/exception/decision group applies to this Go declaration.
    go = measured('a.go', 'package a\n// Store incoming value.\nfunc Store(x int) { x += 1 }')[0]
    documented = measured('a.go', 'package a\n// Store incoming value.\n// @param x Input amount.\nfunc Store(x int) { x += 1 }')[0]
    assert documented['documentationHealth']['score'] - go['documentationHealth']['score'] == 10


def test_all_documentation_cannot_raise_health_or_transparency_by_more_than_five():
    """Perfect docs preserve S/R/organization/dependencies and have a bounded H/T effect."""
    before = measured('a.cs', csharp())[0]
    after = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    assert after['documentationHealth']['score'] == 100
    for metric in ('score', 'transparencyScore'):
        assert after['health'][metric] - before['health'][metric] == pytest.approx(5)
    for metric in ('structuralScore', 'readabilityScore', 'organizationScore'):
        assert before['health'][metric] == after['health'][metric]
    assert before['health']['components']['dependencies'] == after['health']['components']['dependencies']
    assert after['documentationHealth']['semanticReview']['status'] == 'not-reviewed'


@pytest.mark.parametrize('description', ['', ' ', 'TODO', 'TODO explain later', 'FIXME', 'TBD', 'N/A', 'description', 'p0', 'p_0', '!!!'])
def test_empty_placeholder_and_name_only_tags_are_not_parameter_descriptions(description):
    """A tag or a repeated identifier must not grant the small parameter bonus."""
    node = measured('a.cs', csharp(purpose=True, inputs=True, result=True, text=description))[0]
    coverage = node['documentationHealth']['parameterCoverage']
    assert coverage['covered'] == 0 and coverage['missing'] == ['p0']
    assert node['documentationHealth']['score'] == 90
    assert any(f['rule'] == 'missing-parameter-description' and f['severity'] == 'info' for f in node['health']['findings'])


def test_partial_wrong_and_duplicate_tags_do_not_expand_the_denominator_or_bonus():
    """Only real formal names contribute; duplicate and unrelated tags cannot manufacture coverage."""
    source = csharp(2, purpose=True, result=True).replace('int Double', '/// <param name="p0">First amount.</param>\n/// <param name="p0">First amount repeated.</param>\n/// <param name="foreign">Unrelated text.</param>\nint Double')
    node = measured('a.cs', source)[0]
    coverage = node['documentationHealth']['parameterCoverage']
    assert coverage['required'] == 2 and coverage['covered'] == 1 and coverage['coveragePercent'] == 50
    assert coverage['missing'] == ['p1'] and node['documentationHealth']['score'] == 95
    verbose = measured('a.cs', csharp(purpose=True, result=True, inputs=True, text='Input amount. ' * 100))[0]
    concise = measured('a.cs', csharp(purpose=True, result=True, inputs=True))[0]
    assert concise['health']['score'] == verbose['health']['score']
    assert compact_health(node)['documentationHealth']['parameterCoverage'] == coverage


@pytest.mark.parametrize('filename,source,names', [
    ('a.py', 'def doubled(x):\n    """Double an input.\n\n    Args:\n        x: Input amount.\n    """\n    return x * 2\n', ['x']),
    ('a.py', 'def doubled(x):\n    """Double an input.\n\n    :param x: Input amount.\n    """\n    return x * 2\n', ['x']),
    ('a.java', 'class A { /** Double an input.\n * @param x Input amount. */\nint doubled(int x) { return x * 2; }}', ['x']),
    ('a.js', '/** Double an input.\n * @param {number} x Input amount. */\nconst doubled = x => x * 2;', ['x']),
    ('a.ts', '/** Double an input.\n * @param {number} [x=1] Input amount. */\nfunction doubled(x: number = 1) { return x * 2; }', ['x']),
    ('a.go', 'package a\n// Add two inputs.\n// @param x First amount.\n// @param y Second amount.\nfunc Add(x, y int) int { return x + y }', ['x', 'y']),
    ('a.rs', '/// Double an input.\n/// # Arguments\n/// * `x` - Input amount.\nfn doubled(x: i32) -> i32 { x * 2 }', ['x']),
])
def test_language_adapters_capture_named_parameter_descriptions(filename, source, names):
    """Use actual AST fields and owned XML/JSDoc/docstring/GoDoc/Rustdoc evidence."""
    node = measured(filename, source)[0]
    assert node['documentationHealth']['parameterNames'] == names
    coverage = node['documentationHealth']['parameterCoverage']
    assert coverage['required'] == coverage['covered'] == len(names)
    assert coverage['coveragePercent'] == 100


def test_implicit_receivers_no_parameters_and_foreign_comments_are_not_bonus_inputs():
    """self/cls and nonexistent names never become artificial parameter obligations or rewards."""
    for receiver in ('self', 'cls'):
        source = f'class A:\n    def doubled({receiver}, x):\n        """Double an input.\n\n        :param x: Input amount.\n        """\n        return x * 2\n'
        assert measured('a.py', source)[0]['documentationHealth']['parameterNames'] == ['x']
    node = measured('a.cs', csharp(0, purpose=True, result=True))[0]
    assert node['documentationHealth']['parameterCoverage']['status'] == 'not-applicable'
    assert node['documentationHealth']['parameterCoverage']['coveragePercent'] is None
    assert node['documentationHealth']['score'] == 100
    source = '/// <summary>Class purpose.</summary>\n/// <param name="x">Class parameter.</param>\nclass A { int Doubled(int x) { return x * 2; } }'
    assert measured('a.cs', source)[0]['documentationHealth']['parameterCoverage']['covered'] == 0


def test_old_documentation_and_old_policy_do_not_become_current_improvements():
    """Captured v1 documentation is not silently interpreted under the new scoring formula."""
    node = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    legacy = deepcopy(node)
    legacy['documentationHealth']['version'] = 'documentation-coverage-v1'
    health = calculate_health(legacy, node['health']['dependencies'], organization=node['health']['organization'])
    assert health['score'] is health['documentationScore'] is health['transparencyScore'] is None
    assert health['componentCoverage']['documentation']['reason'] == 'documentation-policy-version-mismatch'
    before = {'policyVersion': 'atlas-health-v2', 'analyzerVersion': 'code-health-v4'}
    saved = deepcopy(before)
    after = {'policyVersion': policy()['version'], 'analyzerVersion': node['health']['analyzerVersion']}
    assert compare_assessments(before, after)['verdict'] == 'UNKNOWN' and before == saved


def test_perfect_documentation_cannot_hide_unknown_dependencies_or_critical_nesting():
    """The new small bonus never replaces unavailable structural facts or changes a critical verdict."""
    node = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    assert calculate_health(node, {'status': 'partial'}, organization=node['health']['organization'])['score'] is None
    node['complexity']['maxNestingDepth'] = 6
    result = calculate_health(node, node['health']['dependencies'], organization=node['health']['organization'])
    assert result['verdict'] == 'REVIEW' and any(f['severity'] == 'critical' for f in result['findings'])


def test_unsupported_destructuring_and_parameter_budget_stay_unknown():
    """Unsupported formal patterns and truncation cannot be mistaken for a parameterless method."""
    node = measured('a.js', '/** Calculate amount. */\nfunction f({x}) { return x * 2; }')[0]
    assert node['documentationHealth']['score'] is None
    assert node['documentationHealth']['parameterCoverage']['coveragePercent'] is None
    node = measured('a.cs', csharp(41, purpose=True, inputs=True, result=True))[0]
    assert node['documentationHealth']['status'] == 'partial' and node['documentationHealth']['score'] is None


def test_resolved_inherited_parameter_coverage_retains_missing_advice():
    """Inherited summaries do not invent descriptions for undocumented formal parameters."""
    from atlas.code_health import resolve_inherited_documentation
    nodes = measured('a.cs', '''interface Parent {
/// <summary>Calculate a doubled amount.</summary>
/// <returns>Doubled amount.</returns>
int Run(int x);
}
class Child {
/// <inheritdoc/>
int Run(int x) { return x * 2; }
}''')
    for node, owner in zip(nodes, ['parent', 'child']):
        node['memberFacts'] = {'name': 'Run', 'ownerNodeId': owner}
    resolve_inherited_documentation(nodes, [{'source': 'child', 'target': 'parent', 'relation': 'implements'}])
    documentation = nodes[1]['documentationHealth']
    assert documentation['status'] == 'complete' and documentation['origin'] == 'resolved-inherited-contract'
    assert documentation['parameterCoverage']['missing'] == ['x'] and documentation['score'] == 90
    assert any(item['rule'] == 'missing-parameter-description' for item in documentation['findings'])


@pytest.mark.parametrize('parameters', ['{x} = {}', '[x] = []', '...[x]', '...{x}'])
def test_wrapped_destructuring_is_unknown_not_parameterless(parameters):
    """Default/rest wrappers must not hide unsupported binding patterns behind zero named inputs."""
    node = measured('a.js', '/** Calculate amount. */\nfunction f(' + parameters + ') { return x * 2; }')[0]
    assert node['documentationHealth']['status'] == 'partial'
    assert node['documentationHealth']['score'] is None
    assert node['documentationHealth']['parameterCoverage']['coveragePercent'] is None


def test_js_dollar_names_and_real_python_self_inputs_have_matching_docs():
    """Tag normalization and receiver recognition must agree with actual language bindings."""
    js = measured('a.js', '/** Double an input.\n * @param {number} $x Input amount. */\nfunction f($x) {return $x * 2;}')[0]
    assert js['documentationHealth']['parameterCoverage']['covered'] == 1
    for source in ('def f(self):\n    """Double an input.\n\n    :param self: Explicit free-function input.\n    """\n    return self * 2\n',
                   'class A:\n    @staticmethod\n    def f(self):\n        """Double an input.\n\n        :param self: Explicit static-function input.\n        """\n        return self * 2\n'):
        node = measured('a.py', source)[0]
        assert node['documentationHealth']['parameterNames'] == ['self']
        assert node['documentationHealth']['parameterCoverage']['covered'] == 1


def test_unversioned_documentation_cannot_become_current_complete_health():
    """Missing policy provenance is unknown even when legacy payload supplies a perfect score."""
    node = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    node['documentationHealth'].pop('version')
    result = calculate_health(node, node['health']['dependencies'], organization=node['health']['organization'])
    assert result['score'] is result['documentationScore'] is result['transparencyScore'] is None


def test_displayed_caps_hold_at_half_tenth_boundaries_and_in_forecasts():
    """Decimal half-up arithmetic avoids an apparent 0.6 increase from a raw 0.5 bonus."""
    from atlas.code_health_planning import score_range
    node = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    values = []
    for documentation in (0, 10, 100):
        node['documentationHealth']['score'] = documentation
        result = calculate_health(node, node['health']['dependencies'], organization={'score': 15})
        values.append(result)
        assert score_range(result['components']) == [result['score'], result['score']]
    for metric in ('score', 'transparencyScore'):
        assert values[1][metric] - values[0][metric] == pytest.approx(0.5)
        assert values[2][metric] - values[0][metric] == pytest.approx(5)


def test_legacy_transparency_keeps_original_policy_caption_and_mixed_aggregate_unknown():
    """Historical measured scores must never be labelled with the current policy's weights."""
    from atlas.code_health_comparison import summarize
    old = measured('a.cs', csharp(purpose=True, inputs=True, result=True))[0]
    old['health'].update(version='atlas-health-v2', analyzerVersion='code-health-v4', transparencyScore=80)
    saved = deepcopy(old)
    summary = summarize([old])
    assert summary['transparency']['score'] == 80
    assert summary['transparency']['policyVersions'] == ['atlas-health-v2']
    assert '35%' not in summary['transparency']['basis'] and old == saved
    current = measured('b.cs', csharp(purpose=True, inputs=True, result=True))[0]
    mixed = summarize([old, current])
    assert mixed['meanHealth'] is mixed['transparency']['score'] is None
    assert mixed['transparency']['policyVersions'] == ['atlas-health-v2', 'atlas-health-v3']


def test_aggregate_rounding_keeps_caps_and_preserves_legacy_rounding():
    """Averaging cannot inflate the new bonus or silently change historical v2 means."""
    from atlas.code_health_comparison import summarize
    def summary(version, values):
        """Construct captured numeric rows with explicit policy provenance."""
        nodes = [{'id': str(i), 'health': {'version': version, 'score': value, 'transparencyScore': value}}
                 for i, value in enumerate(values)]
        return summarize(nodes)
    before = summary('atlas-health-v3', [82.2, 82.3])
    after = summary('atlas-health-v3', [82.7, 82.8])
    assert after['meanHealth'] - before['meanHealth'] == pytest.approx(0.5)
    assert after['transparency']['score'] - before['transparency']['score'] == pytest.approx(0.5)
    assert summary('atlas-health-v2', [82.2, 82.3])['meanHealth'] == round((82.2 + 82.3) / 2, 1)
    unknown = summarize([{'id': 'x', 'health': {'score': 100, 'transparencyScore': 100}}])
    assert unknown['meanHealth'] is unknown['transparency']['score'] is None


@pytest.mark.parametrize('tag_name', ['class', '@class'])
def test_csharp_verbatim_identifier_uses_canonical_parameter_name(tag_name):
    """The lexical @ prefix must not separate a formal parameter from its XML name."""
    source = f'class A {{\n/// <summary>Double an input.</summary>\n/// <param name="{tag_name}">Input amount.</param>\nint F(int @class) {{ return @class * 2; }}\n}}'
    documentation = measured('a.cs', source)[0]['documentationHealth']
    assert documentation['parameterNames'] == ['class']
    assert documentation['parameterCoverage']['covered'] == 1
    assert documentation['parameterCoverage']['missing'] == []
