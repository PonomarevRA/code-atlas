"""Ограниченные RAM-результаты архитектуры и чтение закреплённых доказательств."""
from collections import OrderedDict
import json
import threading
import time
from uuid import uuid4

from .architecture_facts import capture
from .architecture_engine import evaluate
from .architecture_rules import Rules
from .source_viewer import read_node_source, SourceError
from .store import StoreError
from .mcp_response_budget import bound_list_response

MAX_RUNS = 16
MAX_RUN_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
TTL_SECONDS = 3600


class ArchitectureChecks:
    """Хранит immutable наблюдения час; после restart/eviction требуется явный новый запуск."""
    def __init__(self, store, clock=time.monotonic):
        self.store, self.clock = store, clock
        self.runs = OrderedDict()
        self.lock = threading.Lock()
        self.analysis_slot = threading.BoundedSemaphore(1)

    def _snapshot(self, project_id, snapshot_id):
        """Читает путь и граф только при совпадении выбранной ревизии; корень берёт из store."""
        root, graph = self.store.comparison_snapshot(project_id)
        if graph.get('metadata', {}).get('snapshotId') != snapshot_id:
            raise StoreError('Карта обновилась. Выберите текущий снимок и повторите проверку.', 409)
        return root, graph

    def run(self, project_id: str, snapshot_id: str, config: Rules) -> dict:
        """Запускает общий движок, отклоняя конкурирующий анализ и слишком большой результат."""
        if not self.analysis_slot.acquire(blocking=False):
            raise StoreError('Архитектурная проверка уже выполняется. Повторите позже.', 429)
        try:
            root, graph = self._snapshot(project_id, snapshot_id)
            result = evaluate(config, capture(root, graph, snapshot_id, config))
            self.store.assert_snapshot(project_id, snapshot_id)
            size = len(json.dumps(result, ensure_ascii=False).encode())
            if size > MAX_RUN_BYTES:
                raise StoreError('Результат превышает лимит 8 MiB. Сузьте области правил.', 413)
            run_id = uuid4().hex
            result.update(runId=run_id, projectId=project_id, retentionSeconds=TTL_SECONDS)
            with self.lock:
                self._expire()
                while self.runs and (len(self.runs) >= MAX_RUNS or sum(item[2] for item in self.runs.values()) + size > MAX_TOTAL_BYTES):
                    self.runs.popitem(last=False)
                self.runs[run_id] = (self.clock(), result, size)
            return self.page(project_id, run_id, 'rules', 0, 20)
        finally:
            self.analysis_slot.release()

    def _expire(self):
        """Вызывается под lock; срок не продлевается чтением или страницей."""
        expired = [key for key, item in self.runs.items() if self.clock() - item[0] >= TTL_SECONDS]
        for key in expired:
            del self.runs[key]

    def _get(self, project_id, run_id):
        """Выдаёт только результат этого проекта; чужой и истёкший ID не раскрываются."""
        with self.lock:
            self._expire()
            entry = self.runs.get(run_id)
            if entry is None or entry[1]['projectId'] != project_id:
                raise StoreError('Результат недоступен. Запустите проверку снова.', 404)
            result = entry[1]
        self.store.assert_snapshot(project_id, result['snapshotId'])
        return result

    def page(self, project_id, run_id, collection='rules', offset=0, limit=20):
        """Возвращает одну страницу правил или кратких нарушений, не пересчитывая вход."""
        result = self._get(project_id, run_id)
        if collection not in {'rules', 'findings'} or offset < 0 or not 1 <= limit <= 20:
            raise StoreError('Некорректная страница результата.', 422)
        values = result[collection]
        items = values[offset:offset + limit]
        if collection == 'findings':
            items = [{key: item[key] for key in ('id', 'ruleId', 'kind')} | {'edgeCount': len(item['path'])} for item in items]
        # Причины тоже ограничены, отдельно обозначая неполную доставку объяснения.
        else:
            items = [dict(item, reasons=item['reasons'][:10],
                          reasonsTruncated=item['reasonsTruncated'] or len(item['reasons']) > 10) for item in items]
        meta = {key: result[key] for key in ('version', 'analyzerVersion', 'rulesFingerprint', 'sourceFingerprint',
                                            'snapshotId', 'runId', 'projectId', 'status', 'counts',
                                            'findingsTruncated', 'retentionSeconds', 'interpretation')}
        return self._bounded_page({**meta, 'collection': collection}, items, offset, len(values))

    def _finding(self, result, finding_id):
        """ID выбирает только сохранённое доказательство; произвольный путь не принимается."""
        for finding in result['findings']:
            if finding['id'] == finding_id:
                return finding
        raise StoreError('Доказательство не найдено.', 404)

    def evidence(self, project_id, run_id, finding_id, offset=0, limit=20):
        """Путь-доказательство читается страницами; усечённый фрагмент не называется всем циклом."""
        result = self._get(project_id, run_id)
        finding = self._finding(result, finding_id)
        if offset < 0 or not 1 <= limit <= 20:
            raise StoreError('Некорректная страница доказательств.', 422)
        path = finding['path']
        end = offset + limit
        return self._bounded_page({'runId': run_id, 'snapshotId': result['snapshotId'],
                                   'sourceFingerprint': result['sourceFingerprint'], 'findingId': finding_id,
                                   'kind': finding['kind'], 'ruleId': finding['ruleId']},
                                  path[offset:end], offset, len(path))

    def _bounded_page(self, meta, items, offset, total):
        """Общий HTTP/MCP byte budget; cursor учитывает реально доставленные элементы."""
        page = {**meta, 'items': items, 'offset': offset, 'total': total,
                # Reserve at least the JSON width of null and every possible cursor.
                'nextOffset': max(total, 9999)}
        bounded = bound_list_response(page, ('items',), flag='deliveryTruncated')
        if bounded is None or (items and not bounded['items']):
            raise StoreError('Один элемент превышает бюджет ответа. Сузьте правила.', 413)
        end = offset + len(bounded['items'])
        bounded['nextOffset'] = end if end < total else None
        return bounded

    def source(self, project_id, run_id, finding_id, edge_index):
        """Читает ограниченный фрагмент только при совпадении snapshot и byte hash доказательства."""
        result = self._get(project_id, run_id)
        path = self._finding(result, finding_id)['path']
        if not 0 <= edge_index < len(path):
            raise StoreError('Ребро не найдено.', 404)
        edge = path[edge_index]
        root, _ = self._snapshot(project_id, result['snapshotId'])
        try:
            source = read_node_source(root, {'file': edge['file'], 'line': edge.get('line')}, result['snapshotId'], include_syntax=False)
        except SourceError as error:
            raise StoreError(str(error), error.status) from error
        if source['sourceByteHash'] != edge['sourceHash']:
            raise StoreError('Исходник изменился. Повторите проверку перед открытием доказательства.', 409)
        self.store.assert_snapshot(project_id, result['snapshotId'])
        line = source['line'] or 1
        lines = source['text'].splitlines()
        start = max(0, line - 6)
        window = lines[start:line + 14]
        window_text = '\n'.join(item[:600] for item in window)
        text = window_text[:12000]
        return {'snapshotId': result['snapshotId'], 'sourceFingerprint': result['sourceFingerprint'],
                'file': edge['file'], 'line': line, 'startLine': start + 1, 'text': text,
                'sourceByteHash': source['sourceByteHash'], 'sourceKind': 'validated-working-tree',
                'truncated': start > 0 or line + 14 < len(lines) or len(window_text) > 12000 or any(len(item) > 600 for item in window)}
