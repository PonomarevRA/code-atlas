"""Coverage проходит настоящий MCP handshake, сохраняя границы фактов и текущего анализа."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import timedelta

import httpx
import pytest
from atlas import agent_mcp, store
from test_grouping_cache import legacy_store
from test_agent_mcp import unpack


@pytest.fixture
def coverage_stdio_server(tmp_path):
    """Публикует временный C# проект и локальный отчёт; никакие тесты проекта не запускаются."""
    state, project_id, snapshot, original = legacy_store(tmp_path)
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'Calculator.cs'
    source.write_text('''class Calculator {
  public int Run(int amount) {
    if (amount < 0) return -1;
    if (amount == 0) return 0;
    if (amount > 10) return 10;
    return amount;
  }
}
''')
    (root / 'CalculatorTests.cs').write_text('''class CalculatorTests {
  [Xunit.Fact]
  public void Negative() { new Calculator().Run(-1); }
  [Xunit.Theory(Skip="Temporarily disabled")]
  [Xunit.InlineData(0)]
  public void Zero(int amount) { new Calculator().Run(amount); }
}
''')
    (root / 'Steps.cs').write_text('''[Binding]
class Steps {
  [When("calculate")]
  public void Calculate() { new Calculator().Run(-1); }
}
''')
    (root / 'Calculator.feature').write_text('''Feature: Boundaries
  Scenario: Negative
    When calculate
  @ignore
  Scenario Outline: Zero
    When calculate
    Examples:
      | amount |
      | 0 |
      | 1 |
''')
    graph = {'nodes': [
        {'id': 'run', 'kind': 'method', 'label': 'Run()', 'file': 'Calculator.cs', 'line': 2},
        {'id': 'negative', 'kind': 'method', 'label': 'Negative()', 'file': 'CalculatorTests.cs', 'line': 2},
        {'id': 'zero', 'kind': 'method', 'label': 'Zero()', 'file': 'CalculatorTests.cs', 'line': 4},
        {'id': 'step', 'kind': 'method', 'label': 'Calculate()', 'file': 'Steps.cs', 'line': 3},
    ], 'edges': [{'source': identifier, 'target': 'run', 'relation': 'calls'}
                 for identifier in ('negative', 'zero', 'step')], 'metadata': original['metadata']}
    store.write_json(snapshot, graph)
    state.projects[project_id]['path'] = str(root)
    store.write_json(state.registry, state.projects)
    state.close()
    report = root / 'coverage.cobertura.xml'
    timestamp = max(time.time_ns(), source.stat().st_mtime_ns) // 1_000_000_000 + 1
    report.write_text(f'''<coverage timestamp="{timestamp}"><packages><package><classes>
<class filename="Calculator.cs"><lines>
<line number="3" hits="1" branch="true" condition-coverage="50% (1/2)"/>
<line number="4" hits="0" branch="true" condition-coverage="0% (0/2)"/>
<line number="5" hits="1" branch="true" condition-coverage="100% (2/2)"/>
</lines></class></classes></package></packages></coverage>''')
    os.utime(report, ns=(timestamp * 1_000_000_000, timestamp * 1_000_000_000))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    log = (tmp_path / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'atlas.cli', '--no-open', '--port', str(port),
                                '--data-dir', str(tmp_path / 'state')], stdout=log, stderr=log)
    try:
        with httpx.Client(trust_env=False, timeout=.3) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path / 'server.log').read_text())
                try:
                    if client.get(f'http://127.0.0.1:{port}/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            else:
                pytest.fail('Atlas did not become ready')
        yield port, project_id, snapshot.parent.name, source, timestamp
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


def test_coverage_real_stdio_pages_details_and_changed_analysis(coverage_stdio_server):
    """Проверяет counter evidence, Gherkin/skip, курсоры и изменённый источник через реальный SDK."""
    port, project_id, snapshot, source, report_timestamp = coverage_stdio_server

    async def scenario():
        """Клиент получает точные ID от сервера и повторяет запросы с analysisId."""
        parameters = StdioServerParameters(command=sys.executable, args=[
            '-m', 'atlas.agent_mcp', '--port', str(port), '--project-id', project_id])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()

                async def coverage(**extra):
                    """Каждый успешный MCP envelope проверяется целиком по UTF-8 байтам."""
                    result = await session.call_tool('get_test_coverage', {'snapshotId': snapshot, 'nodeId': 'run', **extra})
                    assert len(json.dumps(result.model_dump(mode='json'), ensure_ascii=True).encode()) <= 64_000
                    return unpack(result)

                summary = await coverage()
                assert summary['summary']['conditionsByStatus'] == {'partial': 1, 'uncovered': 1, 'covered': 1, 'unknown': 0}
                assert summary['summary']['testsByKind'] == {'unit': 2, 'gherkin': 2}
                assert len(summary['analysisId']) == 64
                assert summary['records'] == []
                seen, offset = [], 0
                while offset is not None:
                    page = await coverage(section='conditions', offset=offset, limit=1, analysisId=summary['analysisId'])
                    seen.extend(row['id'] for row in page['records'])
                    offset = page['pagination']['nextOffset']
                assert len(seen) == len(set(seen)) == 3
                partial = await coverage(section='conditions', runtimeStatus='partial', kind='if')
                detail = await coverage(conditionId=partial['records'][0]['id'])
                assert detail['detail']['runtime']['branchesCovered'] == 1
                assert all(goal['runtimeStatus'] == 'unknown' for goal in detail['detail']['objectives'])
                assert detail['sourceFacts'][0]['stableRead'] is True
                scenarios = await coverage(section='tests', testKind='gherkin')
                disabled = next(row for row in scenarios['records'] if row['declaredStatus'] == 'disabled')
                test_detail = await coverage(testId=disabled['id'])
                assert test_detail['detail']['exampleRows'] == 2
                assert test_detail['detail']['runtime']['status'] == 'unknown'
                assert test_detail['detail']['steps']
                for invalid in ({'snapshotId': 'stale'}, {'projectId': 'foreign'}, {'conditionId': 'missing'},
                                {'reportPaths': ['../outside.xml']}, {'limit': True}, {'offset': -1}):
                    rejected = await session.call_tool('get_test_coverage', {'snapshotId': snapshot, **invalid})
                    assert rejected.isError, invalid
                before = source.read_bytes()
                source.write_bytes(before + b'// changed working source\n')
                changed_time = (report_timestamp + 5) * 1_000_000_000
                os.utime(source, ns=(changed_time, changed_time))
                rejected = await session.call_tool('get_test_coverage', {'snapshotId': snapshot, 'nodeId': 'run',
                    'section': 'conditions', 'analysisId': summary['analysisId'], 'offset': 1})
                assert rejected.isError and 'analysisId=' in rejected.content[0].text
                fresh = await coverage(section='conditions')
                assert fresh['analysisId'] != summary['analysisId']
                assert all(row['runtime']['status'] == 'unknown' for row in fresh['records'])
                assert (await coverage(section='conditions', runtimeStatus='uncovered'))['records'] == []

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    asyncio.run(scenario())


@pytest.mark.parametrize('failure,expected', [(409, 'Снимок обновлён'), (429, 'Лимит параллельного анализа'),
                                           ('timeout', 'Сервер может продолжать анализ')])
def test_coverage_mcp_http_diagnostic_preserves_reason(monkeypatch, failure, expected):
    """Busy/stale и timeout объясняют реальную причину, не подменяя её недоступностью приложения."""
    def handler(request):
        """Имитирует только транспорт; вызывается зарегистрированный SDK инструмент."""
        if failure == 'timeout':
            raise httpx.ReadTimeout('slow', request=request)
        return httpx.Response(failure, json={'error': expected})

    original = httpx.AsyncClient

    def client(**options):
        """Холодный анализ получает timeout120 без proxy и redirect."""
        assert options['timeout'] == 120 and options['trust_env'] is False
        return original(**options, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(agent_mcp.httpx, 'AsyncClient', client)

    async def scenario():
        """Проверяет текст диагностической ошибки на границе FastMCP."""
        with pytest.raises(Exception, match=expected):
            await agent_mcp.create_mcp(project_id='one').call_tool('get_test_coverage', {'snapshotId': 's'})

    asyncio.run(scenario())
