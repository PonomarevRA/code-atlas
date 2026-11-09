import { renderComplexitySummary } from './complexity-summary.js';

/** Размещает подсказку вне карточки: сверху при достаточном месте, иначе снизу.
 * Если целиком не помещается ни с одной стороны, выбирает большую область с прокруткой.
 * Возвращает null, когда карточка вне карты или свободного места даже для краткой панели нет.
 */
export function tooltipPosition(host, anchor, size) {
  const margin = 10, gap = 12;
  const bottom = anchor.bottom ?? anchor.top + (anchor.height || 0);
  const right = anchor.right ?? anchor.left + (anchor.width || 0);
  if (bottom <= host.top || anchor.top >= host.top + host.height
    || right <= host.left || anchor.left >= host.left + host.width) return null;
  const above = Math.max(0, anchor.top - host.top - margin - gap);
  const below = Math.max(0, host.top + host.height - margin - bottom - gap);
  const fitsAbove = size.height <= above;
  const fitsBelow = size.height <= below;
  const placement = fitsAbove || (!fitsBelow && above > below) ? 'above' : 'below';
  const available = placement === 'above' ? above : below;
  if (available < Math.min(80, size.height)) return null;
  const maxHeight = Math.min(size.height, available);
  const maxLeft = Math.max(margin, host.width - size.width - margin);
  const centered = (anchor.left + right) / 2 - host.left - size.width / 2;
  return {
    left: Math.max(margin, Math.min(maxLeft, centered)),
    top: placement === 'above' ? anchor.top - host.top - gap - maxHeight : bottom - host.top + gap,
    maxHeight, placement,
  };
}

/** Безопасный HTML-элемент: данные исходника устанавливаются исключительно через textContent. */
function textElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text != null) element.textContent = String(text);
  return element;
}

/** Hover/focus-подсказка с мостом к кнопкам; закрепление сохраняет сведения при движении по карте. */
export class MapTooltip {
  /** Создаёт один popup в контейнере карты; onDescribe получает исходный ID символа и элемент для возврата фокуса. */
  constructor(host, onDescribe) {
    this.host = host;
    this.onDescribe = onDescribe;
    this.collapsed = false;
    this.expanded = false;
    this.element = textElement('section', 'map-tooltip');
    this.element.hidden = true;
    this.element.style.overflow = 'auto';
    this.element.setAttribute('role', 'region');
    this.element.setAttribute('aria-label', 'Сведения об элементе карты');
    this.element.addEventListener('mouseenter', () => this.cancelHide());
    this.element.addEventListener('mouseleave', () => this.scheduleHide());
    this.element.addEventListener('focusin', () => this.cancelHide());
    this.element.addEventListener('focusout', event => {
      if (!this.element.contains(event.relatedTarget)) this.scheduleHide();
    });
    this.element.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      this.hide(true);
      this.restoreAnchorFocus();
    });
    host.append(this.element);
  }

  /** Отменяет закрытие, когда указатель или фокус переходит от карточки к popup. */
  cancelHide() { clearTimeout(this.hideTimer); }

  /** Короткая задержка образует hover-мост между элементом карты и popup. */
  scheduleHide() {
    this.cancelHide();
    this.hideTimer = setTimeout(() => this.hide(), 220);
  }

  /** Закрывает обычную подсказку; закреплённую закрывает только явное действие. */
  hide(force = false) {
    this.cancelHide();
    if (this.pinned && !force) return;
    this.pinned = false;
    this.collapsed = false;
    this.expanded = false;
    this.element.hidden = true;
    this.anchorObserver?.disconnect();
    this.resizeObserver?.disconnect?.();
  }

  /** Возвращает клавиатурный фокус, подавляя немедленное повторное открытие от события focus. */
  restoreAnchorFocus() {
    this.restoringFocus = true;
    try { this.anchor?.focus?.({ preventScroll: true }); }
    finally { this.restoringFocus = false; }
  }

  /** Показывает краткие сведения, сохраняя закреплённый выбор и границы контейнера. */
  show(model, anchor) {
    if (this.restoringFocus) return;
    this.cancelHide();
    if (this.pinned && this.model?.id !== model.id) return;
    if (this.model?.id !== model.id) { this.collapsed = false; this.expanded = false; }
    this.model = model;
    this.anchor = anchor;
    this.element.replaceChildren();
    this.element.style.setProperty('--tooltip-role-color', model.role.color);
    this.element.append(this.windowHeader(model));
    this.body = textElement('div', 'map-tooltip-body');
    this.appendDetails(model);
    this.element.append(this.body);
    this.syncWindow();
    this.element.hidden = false;
    this.reposition();
    if (!this.element.hidden) this.watchAnchor();
  }

  /** Добавляет только факты исходного model; сворачивание скрывает тело, не удаляя его содержание. */
  appendDetails(model) {
    this.body.append(textElement('strong', 'map-tooltip-role', model.role.label));
    this.body.append(textElement('p', 'map-tooltip-summary', model.role.description));
    if (model.summary) this.body.append(textElement('p', 'map-tooltip-summary', model.summary));
    if (model.complexity) this.body.append(renderComplexitySummary(model.complexity));
    if (model.fieldFacts) {
      const field = model.fieldFacts;
      const nullable = field.nullable === true ? 'NULL' : field.nullable === false ? 'NOT NULL' : 'nullability неизвестна';
      this.body.append(textElement('p', 'map-tooltip-summary', `${field.type} · ${nullable}`));
      this.body.append(textElement('p', 'map-tooltip-summary', field.description || 'Описание в исходнике отсутствует'));
    }
    this.body.append(textElement('p', 'map-tooltip-location', model.location));
    this.body.append(textElement('p', 'map-tooltip-links', `Входящие: ${model.incoming} · Исходящие: ${model.outgoing} · в видимой области`));
    this.body.append(textElement('p', 'map-tooltip-evidence', model.role.evidence));
  }

  /** Создаёт компактный titlebar с оконными controls и прежними действиями исходного узла. */
  windowHeader(model) {
    const header = textElement('header', 'map-tooltip-header');
    const controls = textElement('div', 'map-tooltip-window-controls');
    const close = this.windowButton('close', 'Закрыть подсказку', '×', () => { this.hide(true); this.restoreAnchorFocus(); });
    this.collapseButton = this.windowButton('collapse', 'Свернуть сведения', '−', () => this.windowAction('collapse'));
    this.expandButton = this.windowButton('expand', 'Расширить подсказку', '↗', () => this.windowAction('expand'));
    controls.append(close, this.collapseButton, this.expandButton);
    const actions = textElement('div', 'map-tooltip-actions');
    if (model.canDescribe) {
      const describe = this.iconButton('map-tooltip-describe', 'Параметры и условия', 'M4 7h16M4 17h16M9 4v6M15 14v6', event => {
        event.stopPropagation();
        this.onDescribe(model.id, this.anchor);
        this.hide(true);
      });
      actions.append(describe);
    }
    this.pinButton = this.iconButton('map-tooltip-pin', 'Закрепить подсказку', 'm9 3 6 0-1 6 4 4H6l4-4-1-6M12 13v8', () => { this.pinned = !this.pinned; this.syncWindow(); });
    actions.append(this.pinButton);
    const title = textElement('h3', 'map-tooltip-title', model.label); title.title = model.label;
    header.append(controls, title, actions);
    return header;
  }

  /** Оконное действие сохраняет popup при уходе мыши: иначе свёрнутый заголовок потеряется на hover-out. */
  windowAction(action) {
    this.pinned = true;
    if (action === 'collapse') this.collapsed = !this.collapsed;
    if (action === 'expand') { this.collapsed = false; this.expanded = !this.expanded; }
    this.syncWindow();
    this.reposition();
  }

  /** Обновляет только представление и доступные состояния кнопок, сохраняя модель и камеру карты. */
  syncWindow() {
    this.body.hidden = this.collapsed;
    this.element.classList.toggle('is-collapsed', this.collapsed);
    this.element.classList.toggle('is-expanded', this.expanded);
    this.collapseButton.setAttribute('aria-expanded', String(!this.collapsed));
    const collapseLabel = this.collapsed ? 'Развернуть сведения' : 'Свернуть сведения';
    this.collapseButton.setAttribute('aria-label', collapseLabel); this.collapseButton.title = collapseLabel;
    this.expandButton.setAttribute('aria-pressed', String(this.expanded));
    const expandLabel = this.expanded ? 'Вернуть компактный размер' : 'Расширить подсказку';
    this.expandButton.setAttribute('aria-label', expandLabel); this.expandButton.title = expandLabel;
    this.pinButton.setAttribute('aria-pressed', String(Boolean(this.pinned)));
    const pinLabel = this.pinned ? 'Открепить подсказку' : 'Закрепить подсказку';
    this.pinButton.setAttribute('aria-label', pinLabel); this.pinButton.title = pinLabel;
  }

  /** Создаёт одну цветную оконную кнопку с невидимым для screen reader hover-символом. */
  windowButton(action, label, glyph, handler) {
    const button = textElement('button', `map-tooltip-${action}`);
    button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    const icon = textElement('span', '', glyph); icon.setAttribute('aria-hidden', 'true'); button.append(icon);
    button.addEventListener('click', handler);
    return button;
  }

  /** Создаёт SVG-иконку только из статического path; подпись и данные узла никогда не становятся markup. */
  iconButton(className, label, path, handler) {
    const button = textElement('button', className);
    button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('aria-hidden', 'true');
    const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path'); shape.setAttribute('d', path);
    icon.append(shape); button.append(icon); button.addEventListener('click', handler);
    return button;
  }

  /** Пересчитывает экранные границы после pan/zoom; изменение позиции не меняет выбор или фокус. */
  reposition() {
    if (this.element.hidden) return;
    if (this.anchor?.isConnected === false) { this.hide(true); return; }
    const bounds = this.host.getBoundingClientRect();
    this.element.style.maxWidth = `${Math.max(0, bounds.width - 20)}px`;
    // Сначала измеряем полную высоту в пределах карты: прежнее ограничение не должно менять выбор стороны.
    this.element.style.maxHeight = `${Math.max(0, bounds.height - 20)}px`;
    const position = tooltipPosition(bounds, this.anchor.getBoundingClientRect(), this.element.getBoundingClientRect());
    if (!position) { this.hide(true); return; }
    this.element.style.maxHeight = `${position.maxHeight}px`;
    this.element.style.left = `${position.left}px`;
    this.element.style.top = `${position.top}px`;
    this.element.dataset.placement = position.placement;
  }

  /** Наблюдает только SVG transforms и размер карты, пока подсказка видна; свои style-правки не запускают цикл. */
  watchAnchor() {
    if (typeof MutationObserver === 'function') {
      this.anchorObserver ||= new MutationObserver(() => this.reposition());
      this.anchorObserver.observe(this.host, {subtree: true, attributes: true, attributeFilter: ['transform']});
    }
    if (typeof ResizeObserver === 'function') {
      this.resizeObserver ||= new ResizeObserver(() => this.reposition());
      this.resizeObserver.observe(this.host);
    }
  }
}
