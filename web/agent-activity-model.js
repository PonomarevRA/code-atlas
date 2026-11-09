/** Ограниченная проекция журнала; порядок задаёт decimal sequence, а не время часов. */
export class AgentActivityModel {
  /** Создаёт пустую клиентскую проекцию с максимум limit строками, без browser storage. */
  constructor(limit = 500) {
    this.limit = limit;
    this.reset();
  }
  /** Удаляет прежнее поколение и курсор при reset или смене проекта. */
  reset() {
    this.events = [];
    this.ids = new Set();
    this.cursor = null;
    this.agents = new Map();
    this.latestSequence = null;
  }
  /** Применяет events целиком до ACK, сохраняет cursor; дубли и старые sequence не добавляются в ленту. */
  apply(events, cursor) {
    for (const event of events) {
      if (!event || typeof event.eventId !== 'string' || this.ids.has(event.eventId)) continue;
      if (!/^[0-9]+$/.test(event.sequence || '')) continue;
      if (this.latestSequence !== null && BigInt(event.sequence) <= BigInt(this.latestSequence)) continue;
      this.events.push(event);
      this.latestSequence = event.sequence;
      this.ids.add(event.eventId);
      const key = activityIdentity(event);
      this.agents.delete(key);
      this.agents.set(key, event);
      while (this.events.length > this.limit) this.ids.delete(this.events.shift().eventId);
      while (this.agents.size > 100) this.agents.delete(this.agents.keys().next().value);
    }
    if (cursor) this.cursor = cursor;
  }
}

/** Возвращает ключ сессии события event; отдельный агент возможен только при подтверждённом identityQuality. */
export function activityIdentity(event) {
  return `${event.sessionId || 'unknown'}:${event.identityQuality === 'agent' ? event.agentId || 'unknown' : 'session'}`;
}

/** Возвращает текстовую подпись события event из разрешённых полей без вызова модели и HTML-интерполяции. */
export function activityLabel(event) {
  const names = { search: 'Поиск', read: 'Чтение', describe: 'Описание', neighbors: 'Связи', recall: 'Поиск памяти', context: 'Контекст', remember: 'Сохранение', status: 'Статус' };
  let text = names[event.operation] || event.operation || 'Операция';
  if (event.query?.mode === 'preview') text += `: ${event.query.preview || ''}${event.query.truncated ? '…' : ''}`;
  // Finished описывает наблюдаемое завершение, даже когда успешность неизвестна.
  if (event.phase === 'finished') {
    if (event.outcome === 'failed') text += ' · ошибка';
    else if (event.outcome === 'cancelled') text += ' · отменено';
    else text += ' · завершено';
  }
  if (Number.isFinite(event.resultCount)) text += ` · ${event.resultCount}`;
  return text;
}

/** Разрешает точный маркер только для того же проекта, snapshot и видимого raw ID. */
export function activityTarget(event, context, visibleIds) {
  const target = event.target;
  if (target?.status === 'unavailable') return { status: 'unavailable' };
  if (!target?.nodeId) return { status: 'none' };
  if (target.projectId !== context.projectId) return { status: 'other-project' };
  if (!target.snapshotId || target.snapshotId !== context.snapshotId) return { status: 'stale' };
  if (!visibleIds.has(target.nodeId)) return { status: 'outside' };
  return { status: 'visible', nodeId: target.nodeId };
}

/** Возвращает связанные пары Запрос → Ответ по session/operationId в порядке первого наблюдения. */
export function activityOperations(events) {
  const operations = new Map();
  for (const event of events) {
    const key = `${event.sessionId || 'unknown'}:${event.operationId || event.eventId}`;
    let operation = operations.get(key);
    if (!operation) { operation = { started: null, finished: null, event }; operations.set(key, operation); }
    if (event.phase === 'started') operation.started = event;
    if (event.phase === 'finished') operation.finished = event;
    operation.event = operation.finished || operation.started || event;
  }
  return [...operations.values()];
}
