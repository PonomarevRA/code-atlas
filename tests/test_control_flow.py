"""Ветви и документация подтверждаются AST выбранного метода, а не текстовыми совпадениями."""
import json
import pytest
from atlas.execution_path import build_execution_path


@pytest.fixture
def branch_project(tmp_path):
    """Создаёт вложенные/альтернативные ветви, guard, expression arms и соседний метод."""
    project = tmp_path / 'source'
    project.mkdir()
    text = '''class Handler {
    /// <summary>Processes &lt;items&gt; safely.</summary>
    /// <remarks>Keep original order.</remarks>
    /// <param name="x">Input value.</param>
    /// <returns>Selected result.</returns>
    [Obsolete]
    public int Run(int x)
    {
        if (Check(x)) {
            logger.LogInformation("OUTER_TRUE");
            if (x > 1) Accept(); else Reject();
        } else if (x == 0) {
            Zero();
        } else {
            Negative();
        }
        switch (x) {
            case 1:
            case 2: Pair(); break;
            case > 3 when Allowed(x): Guarded(); break;
            default: throw new Exception("NONE");
        }
        var value = x switch { 0 => Empty(), > 0 when Allowed(x) => Positive(), _ => Other() };
        var fake = "if (fake) Pretend(); switch(x) { default: break; }";
        // if (fake) CommentCall();
        return value;
    }
    /// <summary>NEIGHBOR_DOC</summary>
    public void Neighbor() { if (true) NeighborOnly(); }
}
'''
    (project / 'Handler.cs').write_text(text)
    graph = {'nodes': [{'id': 'run', 'label': 'Run', 'file': 'Handler.cs', 'line': 7, 'kind': 'method'}], 'edges': [], 'metadata': {}}
    return project, graph


def analyzed_step(branch_project):
    """Получает публичный результат интегрированного анализа одного метода."""
    project, graph = branch_project
    return build_execution_path(project, graph, 'run', 's')['steps'][0]


def test_control_flow_exact_if_membership(branch_project):
    """Условия не принадлежат своим arms; соседние then/else взаимоисключающи."""
    step = analyzed_step(branch_project)
    branches = step['controlFlow']['branches']
    arms = {arm['id']: arm for branch in branches for arm in branch['arms']}
    events = {event.get('callee'): event for event in step['events'] if event.get('callee')}
    assert events['Check']['branchIds'] == []
    accept = set(events['Accept']['branchIds'])
    reject = set(events['Reject']['branchIds'])
    zero = set(events['Zero']['branchIds'])
    negative = set(events['Negative']['branchIds'])
    assert len(accept) == len(reject) == 2
    assert len(accept & reject) == 1
    assert len(zero & negative) == 1
    assert not accept & zero
    assert all(identifier in arms for event in step['events'] for identifier in event['branchIds'])
    assert 'NeighborOnly' not in events and 'Pretend' not in events and 'CommentCall' not in events


def test_switch_arms_guards_and_terminals(branch_project):
    """Case/default/when и return/throw/break сохраняют позиции и конкретные arms."""
    step = analyzed_step(branch_project)
    flow = step['controlFlow']
    switch = next(branch for branch in flow['branches'] if branch['kind'] == 'switch')
    expression = next(branch for branch in flow['branches'] if branch['kind'] == 'switch-expression')
    assert any(arm['kind'] == 'default' for arm in switch['arms'])
    assert any('Allowed' in json.dumps(arm) for arm in switch['arms'])
    assert len(expression['arms']) == 3
    events = {event.get('callee'): event for event in step['events'] if event.get('callee')}
    assert set(events['Pair']['branchIds']).isdisjoint(events['Guarded']['branchIds'])
    assert events['Positive']['branchIds']
    assert set(events['Positive']['branchIds']).isdisjoint(events['Other']['branchIds'])
    terminals = flow['terminals']
    assert len([item for item in terminals if item['kind'] == 'break']) == 2
    assert next(item for item in terminals if item['kind'] == 'throw')['branchIds']
    assert next(item for item in terminals if item['kind'] == 'return')['branchIds'] == []
    assert all(item['line'] <= item['endLine'] for item in terminals)
    assert flow['truncated'] is False


def test_compact_switch_cases_are_not_merged(branch_project):
    """Пробел после ':' не меняет принадлежность разных case-тел."""
    project, graph = branch_project
    (project / 'Handler.cs').write_text('class Handler{int Run(int x){switch(x){case 1:return A();case 2:return B();default:return C();}}}')
    step = analyzed_step(branch_project)
    branch = step['controlFlow']['branches'][0]
    assert len(branch['arms']) == 3
    events = {event['callee']: event for event in step['events'] if event.get('callee')}
    assert set(events['A']['branchIds']).isdisjoint(events['B']['branchIds'])


def test_selected_documentation_signature_and_type_context(branch_project):
    """XML перед атрибутом относится к методу; документация соседнего метода исключена."""
    project, graph = branch_project
    source = project / 'Handler.cs'
    source.write_text('/// <summary>TYPE_DOC</summary>\n' + source.read_text())
    step = analyzed_step(branch_project)
    docs = step['documentation']
    assert docs['summary'] == 'Processes <items> safely.'
    assert docs['remarks'] == 'Keep original order.'
    assert docs['returns'] == 'Selected result.'
    assert docs['params'] == [{'name': 'x', 'text': 'Input value.'}]
    assert step['parameters'] == [{'name': 'x', 'type': 'int'}]
    assert step['returnType'] == 'int'
    assert 'public int Run(int x)' in step['signature']
    assert 'OUTER_TRUE' not in step['signature']
    assert step['typeContext']['documentation']['summary'] == 'TYPE_DOC'
    assert 'NEIGHBOR_DOC' not in json.dumps(step)


@pytest.mark.parametrize('xml', ['<summary>unfinished', '<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><summary>&x;</summary>'])
def test_malformed_or_entity_xml_stays_literal(branch_project, xml):
    """Неподдержанный XML отмечается; содержимое сущностей не раскрывается."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text(f'class Handler {{\n/// {xml}\nvoid Run() {{}}\n}}')
    docs = analyzed_step(branch_project)['documentation']
    assert docs['unsupported'] is True
    assert docs['summary'] == ''
    assert xml in docs['comments'][0]['text']


def test_documentation_truncation_is_explicit(branch_project):
    """Локальное обрезание XML-текста также поднимает общий флаг ограничений."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text('class Handler {\n/// <summary>' + 'x' * 3000 + '</summary>\nvoid Run() {}\n}')
    docs = analyzed_step(branch_project)['documentation']
    assert len(docs['summary']) <= 2000
    assert docs['truncated'] is True


def test_control_flow_limits_and_unsupported_are_explicit(branch_project):
    """Большой AST ограничен; try по-прежнему не моделируется."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text('class Handler { void Run() {' + 'if(x)A();' * 110 + 'while(x){try{B();}catch{C();}}} }')
    flow = analyzed_step(branch_project)['controlFlow']
    assert len(flow['branches']) <= 100
    assert flow['truncated'] is True
    assert flow['unsupported']


def test_loop_body_groups_calls_and_skips_header(tmp_path):
    """Тело цикла получает одну ветвь body; вызовы заголовка в неё не входят."""
    project = tmp_path / 'source'
    project.mkdir()
    (project / 'Handler.cs').write_text('''class Handler {
    void Run() {
        for (var i = Start(); i < Limit(); i++) Body();
        foreach (var item in Items()) Each();
        while (Check()) Again();
        do { Once(); } while (Done());
        while (x) { if (Ready()) Inner(); }
    }
}
''')
    graph = {'nodes': [{'id': 'run', 'label': 'Run', 'file': 'Handler.cs', 'line': 2, 'kind': 'method'}], 'edges': [], 'metadata': {}}
    step = build_execution_path(project, graph, 'run', 's')['steps'][0]
    events = {event.get('callee'): event for event in step['events'] if event.get('callee')}
    loops = {branch['kind']: branch for branch in step['controlFlow']['branches'] if branch['kind'] in {'for', 'foreach', 'while', 'do'}}
    assert set(loops) == {'for', 'foreach', 'while', 'do'}
    for branch in loops.values():
        assert [arm['kind'] for arm in branch['arms']] == ['body']
    body_ids = {branch['arms'][0]['id'] for branch in loops.values()}
    assert events['Start']['branchIds'] == []
    assert events['Limit']['branchIds'] == []
    assert events['Done']['branchIds'] == []
    assert set(events['Body']['branchIds']) <= body_ids
    assert events['Body']['branchIds']
    assert events['Each']['branchIds']
    assert events['Again']['branchIds']
    assert events['Once']['branchIds']
    inner = next(branch for branch in step['controlFlow']['branches'] if branch['kind'] == 'if')
    assert set(inner['parentArmIds']) & body_ids
    assert set(events['Inner']['branchIds']) >= set(inner['parentArmIds'])
    assert loops['foreach']['condition'] == ''
    assert loops['foreach']['collection'] == 'Items()'
    assert loops['foreach']['header'] == 'foreach (var item in Items())'
    assert loops['for']['header'] == 'for (var i = Start(); i < Limit(); i++)'
    assert 'Body()' not in loops['for']['header']
    assert loops['do']['header'] == ''


def test_call_edges_expose_only_unambiguous_current_branch_context(branch_project):
    """Точное sourceLine связывает ветвь; одинаковое имя в обеих arms не угадывается."""
    project, graph = branch_project
    graph['nodes'].append({'id': 'accept', 'label': 'Accept', 'kind': 'external'})
    graph['edges'].append({'source': 'run', 'target': 'accept', 'relation': 'calls', 'line': 11})
    result = build_execution_path(project, graph, 'run', 's')
    edge = result['edges'][0]
    assert edge['branchContextKnown'] is True
    assert len(edge['branchIds']) == 2
    (project / 'Handler.cs').write_text('class Handler { void Run(){if(x)Accept();else Accept();} }')
    graph['edges'][0]['line'] = 1
    result = build_execution_path(project, graph, 'run', 's')
    assert result['edges'][0]['branchContextKnown'] is False


def test_conditional_expression_nested_arms_and_condition_calls(branch_project):
    """?: сохраняет обе альтернативы и вложенность, включая condition-call и throw-expression."""
    from copy import deepcopy
    from atlas.node_description import build_node_description
    project, graph = branch_project
    source = project / 'Handler.cs'
    text = '''class Handler {
      int Run(int x) => Check(x)
        ? (Inner(x) ? Accept() : Reject())
        : (x > 0 ? Zero() : throw Failure());
    }'''
    source.write_text(text)
    graph['nodes'].extend({'id': name.lower(), 'label': name, 'kind': 'external'}
                          for name in ['Accept', 'Reject', 'Zero', 'Failure'])
    graph['edges'].extend({'source': 'run', 'target': name.lower(), 'relation': 'calls', 'line': line}
                          for name, line in [('Accept', 3), ('Reject', 3), ('Zero', 4), ('Failure', 4)])
    before = deepcopy(graph)
    result = build_execution_path(project, graph, 'run', 's')
    step = result['steps'][0]
    outer, yes, no = step['controlFlow']['branches']
    assert [branch['kind'] for branch in [outer, yes, no]] == ['conditional'] * 3
    assert outer['condition'] == 'Check(x)'
    assert [arm['kind'] for arm in outer['arms']] == ['true', 'false']
    assert yes['parentArmIds'] == [outer['arms'][0]['id']]
    assert no['parentArmIds'] == [outer['arms'][1]['id']]
    assert no['arms'][1]['expression'] == 'throw Failure()'
    events = {event['callee']: event for event in step['events'] if event.get('callee')}
    assert events['Check']['branchIds'] == [] and events['Check']['conditional'] is False
    assert events['Inner']['branchIds'] == yes['parentArmIds']
    assert events['Inner']['conditional'] is True
    assert events['Accept']['branchIds'] == yes['parentArmIds'] + [yes['arms'][0]['id']]
    assert events['Reject']['branchIds'] == yes['parentArmIds'] + [yes['arms'][1]['id']]
    assert events['Failure']['branchIds'] == no['parentArmIds'] + [no['arms'][1]['id']]
    assert step['controlFlow']['terminals'][0]['branchIds'] == events['Failure']['branchIds']
    assert all(edge['branchContextKnown'] and len(edge['branchIds']) == 2 for edge in result['edges'])
    assert step['controlFlow']['unsupported'] == [] and not step['controlFlow']['truncated']
    assert build_node_description(project, graph, 'run', 's')['controlFlow'] == step['controlFlow']
    assert build_execution_path(project, graph, 'run', 's')['steps'][0]['controlFlow'] == step['controlFlow']
    assert graph == before and source.read_text() == text


def test_conditional_expression_parameters_literals_and_non_ternary_syntax(branch_project):
    """Байтовые диапазоны не смешивают arms на одной строке и не превращают ?, ??, ?. в ?: ."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text('''class Handler {
      object Run(int? x) {
        var note = "?: if (false)";
        // x ? Fake() : Other();
        var y = x?.ToString() ?? "нет";
        return x != null ? "да" : null;
      }
    }''')
    step = analyzed_step(branch_project)
    branch, = step['controlFlow']['branches']
    assert branch['kind'] == 'conditional' and branch['condition'] == 'x != null'
    assert [arm['expression'] for arm in branch['arms']] == ['"да"', 'null']
    condition, = step['parameterFacts']['conditions']
    assert condition['kind'] == 'conditional' and condition['branchId'] == branch['id']
    assert condition['parameterReferences'] == ['x']
    assert not step['controlFlow']['truncated']


def test_conditional_in_condition_preserves_branch_ownership(branch_project):
    """Вложенный выбор в условии не входит ни в одну альтернативу внешнего ?: ."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text('class Handler{int Run()=> (Test() ? Ready() : Fallback()) ? A() : B();}')
    step = analyzed_step(branch_project)
    outer, inner = step['controlFlow']['branches']
    assert outer['parentArmIds'] == inner['parentArmIds'] == []
    events = {event['callee']: event for event in step['events'] if event.get('callee')}
    assert events['Test']['conditional'] is False
    assert events['Ready']['branchIds'] == [inner['arms'][0]['id']]
    assert events['A']['branchIds'] == [outer['arms'][0]['id']]


def test_conditional_expression_limits_are_explicit(branch_project):
    """Ограничения числа и длины выражений остаются явными и для тернарных ветвей."""
    project, _ = branch_project
    (project / 'Handler.cs').write_text('class Handler{void Run(){' + 'var x = flag ? 1 : 2;' * 110 + '}}')
    flow = analyzed_step(branch_project)['controlFlow']
    assert len(flow['branches']) == 100 and flow['truncated'] is True
    (project / 'Handler.cs').write_text('class Handler{string Run()=>flag ? "' + 'x' * 1100 + '" : null;}')
    flow = analyzed_step(branch_project)['controlFlow']
    assert len(flow['branches'][0]['arms'][0]['expression']) == 1000
    assert flow['truncated'] is True
