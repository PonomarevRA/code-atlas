import { activityLabel, activityTarget } from './agent-activity-model.js';

/** Экранные метки следуют за реальными SVG-карточками, не меняя layout и камеру. */
export class AgentActivityMarkers {
  /** Создаёт экранный слой для SVG view; getContext возвращает текущие projectId/snapshotId. */
  constructor(view, getContext) {
    this.view = view;
    this.getContext = getContext;
    this.root = document.createElement('div');
    this.root.className = 'agent-activity-markers';
    this.root.setAttribute('aria-hidden', 'true');
    document.body.append(this.root);
    this.enabled = false;
    this.events = [];
  }
  /** Запоминает enabled и events, объединяет burst событий и перемещения камеры в один кадр. */
  update(enabled = this.enabled, events = this.events) {
    this.enabled = enabled;
    this.events = events;
    if (this.pending) return;
    this.pending = requestAnimationFrame(() => { this.pending = null; this.render(); });
  }
  /** Возвращает Map raw ID → видимый SVG-элемент; агрегированная группа не выдаётся за исходный метод. */
  anchors() {
    const anchors = new Map();
    for (const [id, item] of this.view.nodeElements) if (item.group?.isConnected) anchors.set(id, item.group);
    for (const group of this.view.svg.querySelectorAll('[data-node-id], [data-raw-id]')) {
      const id = group.getAttribute('data-raw-id') || group.getAttribute('data-node-id');
      if (id && !anchors.has(id)) anchors.set(id, group);
    }
    return anchors;
  }
  /** Показывает ограниченное число последних мест; скрытые и stale цели остаются только в истории. */
  render() {
    this.root.replaceChildren();
    if (!this.enabled) return;
    const context = this.getContext();
    const anchors = this.anchors();
    const visibleIds = new Set(anchors.keys());
    const viewport = this.view.svg.getBoundingClientRect();
    let count = 0;
    for (const event of this.events) {
      const target = activityTarget(event, context, visibleIds);
      if (target.status !== 'visible') continue;
      const rect = anchors.get(target.nodeId).getBoundingClientRect();
      if (!rect.width || !rect.height) continue;
      // Невидимые карточки не дают ложного маркера на краю окна после pan или фильтрации.
      const outsideHorizontally = rect.right < viewport.left || rect.left > viewport.right;
      const outsideVertically = rect.bottom < viewport.top || rect.top > viewport.bottom;
      if (outsideHorizontally || outsideVertically) continue;
      const marker = document.createElement('div');
      marker.className = `agent-activity-marker${event.phase === 'finished' ? ' is-idle' : ''}`;
      const left = Math.max(viewport.left, Math.min(rect.right, viewport.right - 100));
      marker.style.left = `${left}px`;
      marker.style.maxWidth = `${Math.max(60, Math.min(240, viewport.right - left - 8))}px`;
      marker.style.top = `${Math.max(viewport.top, rect.top)}px`;
      marker.style.setProperty('--agent-color', agentColor(event.sessionId || 'unknown'));
      marker.textContent = `${event.clientKind || 'unknown'} · ${activityLabel(event)}`;
      this.root.append(marker);
      if (++count >= 40) break;
    }
  }
}

/** Возвращает CSS hsl по эфемерному id сессии; цвет не подтверждает идентичность агента. */
export function agentColor(id) {
  let hash = 0;
  for (const character of id) hash = (hash * 31 + character.codePointAt(0)) | 0;
  return `hsl(${Math.abs(hash) % 360} 65% 46%)`;
}
