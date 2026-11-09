const STORAGE_KEY = 'atlas-map-chrome-v1';

/** Сворачивает оболочку карты без пересоздания SVG, запросов и изменения Browser History. */
export class MapChrome {
  /** Боковые панели сохраняют собственные предпочтения; режим фокуса временно скрывает их. */
  constructor({ body, toolbar, search, controls, discovery, panels, toggles, context, canvas, canvasControls, storage, activitySearch = false, compact = false, bottomInset = 0, onLayout = () => {} }) {
    Object.assign(this, { body, toolbar, search, controls, discovery, panels, toggles, context, canvas, canvasControls });
    this.document = body.ownerDocument || globalThis.document;
    this.viewport = this.document?.defaultView;
    this.activitySearch = activitySearch; this.compact = compact; this.bottomInset = bottomInset;
    this.onLayout = onLayout;
    try { this.storage = storage === undefined ? globalThis.localStorage : storage; }
    catch { this.storage = null; }
    this.state = { search: true, controls: true, canvas: true, focus: false };
    this.readPreferences();
    this.callbacks = {
      search: () => this.setOpen('search', !this.state.search),
      controls: () => this.setOpen('controls', !this.state.controls),
      canvas: () => this.setOpen('canvas', !this.state.canvas),
      focus: () => this.setFocus(!this.state.focus),
    };
    for (const [name, callback] of Object.entries(this.callbacks)) {
      if (name !== 'search' || !activitySearch) toggles[name].addEventListener('click', callback);
    }
    this.resize = () => this.updateHeight();
    this.viewport?.addEventListener('resize', this.resize);
    this.viewport?.addEventListener('scroll', this.resize, { passive: true });
    if (this.viewport?.ResizeObserver) {
      this.sizeObserver = new this.viewport.ResizeObserver(this.resize);
      for (const element of [toolbar, controls, discovery.root]) this.sizeObserver.observe(element);
    }
    if (this.viewport?.MutationObserver) {
      this.contentObserver = new this.viewport.MutationObserver(() => { this.updateContext(); this.updateHeight(); });
      this.contentObserver.observe(controls, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
    }
    if (this.state.focus) this.closePanels();
    this.apply();
  }

  /** Проверяет только boolean-состояние и компактный снимок прежнего вида; сломанный storage не мешает карте. */
  readPreferences() {
    try {
      const value = this.storage?.getItem(STORAGE_KEY);
      if (!value || value.length > 2048) return;
      const saved = JSON.parse(value);
      if (!['search', 'controls', 'focus'].every(name => typeof saved[name] === 'boolean')) return;
      this.state = { search: saved.search, controls: saved.controls, canvas: typeof saved.canvas === 'boolean' ? saved.canvas : !saved.focus, focus: saved.focus };
      const restore = saved.restore;
      if (restore && ['search', 'controls', 'left', 'right', 'mobile'].every(name => typeof restore[name] === 'boolean')) this.restore = { ...restore, canvas: typeof restore.canvas === 'boolean' ? restore.canvas : true };
    } catch { /* Приватный режим и повреждённые настройки не блокируют управление. */ }
  }

  /** Сохраняет только настройки оболочки, без адресов, карты или запросов поиска. */
  remember() {
    try { this.storage?.setItem(STORAGE_KEY, JSON.stringify({ ...this.state, restore: this.restore || null })); }
    catch { /* Видимость остаётся управляемой, даже если запись недоступна. */ }
  }

  /** Скрытая группа недоступна клавиатуре; фокус возвращается к её внешней кнопке. */
  setGroup(element, open, toggle) {
    if (!open && element.contains(this.document?.activeElement)) toggle.focus?.({ preventScroll: true });
    element.hidden = !open;
    element.inert = !open;
    element.setAttribute('aria-hidden', String(!open));
    toggle.setAttribute('aria-expanded', String(open));
  }

  /** Согласует ARIA и минимальную полосу, оставляя поиск и меню независимыми. */
  apply() {
    if (!this.state.search) this.discovery.setOpen(false);
    this.setGroup(this.search, this.state.search, this.toggles.search);
    this.setGroup(this.controls, this.state.controls, this.toggles.controls);
    this.setGroup(this.canvasControls, this.state.canvas, this.toggles.canvas);
    this.toggles.canvas.setAttribute('aria-label', this.state.canvas ? 'Свернуть управление масштабом' : 'Показать управление масштабом');
    this.toggles.canvas.title = this.toggles.canvas.getAttribute('aria-label');
    if (!this.activitySearch) {
      this.toggles.search.textContent = `Поиск ${this.state.search ? '▴' : '▾'}`;
      this.toggles.search.setAttribute('aria-label', this.state.search ? 'Свернуть поиск' : 'Показать поиск');
      this.toggles.search.title = this.toggles.search.getAttribute('aria-label');
    }
    this.toggles.controls.textContent = `Карта ${this.state.controls ? '▴' : '▾'}`;
    this.toggles.controls.setAttribute('aria-label', this.state.controls ? 'Свернуть управление картой' : 'Показать управление картой');
    this.toggles.controls.title = this.toggles.controls.getAttribute('aria-label');
    this.toggles.focus.textContent = this.state.focus ? 'Вернуть меню' : 'Только карта';
    this.toggles.focus.setAttribute('aria-pressed', String(this.state.focus));
    this.toggles.focus.title = this.state.focus ? 'Вернуть прежние панели' : 'Свернуть все панели для просмотра карты';
    this.body.classList.toggle('map-focus', this.state.focus);
    this.body.classList.toggle('map-search-collapsed', !this.state.search);
    this.body.classList.toggle('map-controls-collapsed', !this.state.controls);
    this.updateContext();
    this.updateHeight();
    this.onLayout();
  }

  /** Открытие возвращает исходные элементы и значения; не создаёт новый поиск или анализ. */
  setOpen(name, open, { focus = true, remember = true } = {}) {
    if (!['search', 'controls', 'canvas'].includes(name) || typeof open !== 'boolean' || this.disposed) return;
    this.state[name] = open;
    this.apply();
    if (remember) this.remember();
    if (focus && open && name === 'search') {
      this.search.querySelector('input:not([hidden]):not([disabled])')?.focus?.({ preventScroll: true });
    }
  }

  /** Закрывает обе панели временно, сохраняя desktop/mobile предпочтения владельца. */
  closePanels() {
    this.applyingPanels = true;
    for (const side of ['left', 'right']) this.panels.setOpen(side, false, { remember: false });
    this.lastPanelState = { ...this.panels.state };
    this.applyingPanels = false;
  }

  /** Режим фокуса обратим: прежние меню и боковые панели восстанавливаются одним нажатием. */
  setFocus(focus) {
    if (this.disposed || this.state.focus === focus) return;
    if (focus) {
      this.restore = { search: this.state.search, controls: this.state.controls, canvas: this.state.canvas, ...this.panels.state, mobile: this.panels.mobile };
      this.state = { search: false, controls: false, canvas: false, focus: true };
      this.closePanels();
      this.viewport?.scrollTo?.({ top: 0, behavior: 'instant' });
    } else {
      const restore = this.restore || { search: true, controls: true, canvas: true };
      this.state = { search: restore.search, controls: restore.controls, canvas: restore.canvas, focus: false };
      const sides = restore.mobile === this.panels.mobile ? restore : this.panels.savedPreferences[this.panels.mobile ? 'mobile' : 'desktop'];
      this.applyingPanels = true;
      for (const side of ['left', 'right']) this.panels.setOpen(side, Boolean(sides[side]), { remember: false });
      this.applyingPanels = false;
      this.restore = null;
    }
    this.apply();
    this.remember();
  }

  /** Ручной вызов панели в режиме фокуса учитывается при возврате; смена viewport не раскрывает панели сама. */
  syncPanels(info) {
    if (!this.state.focus || this.applyingPanels) return;
    if (info?.reason === 'reveal') { this.lastPanelState = { ...this.panels.state }; return; }
    if (info?.reason === 'viewport') { this.closePanels(); return; }
    if (this.restore && this.restore.mobile === this.panels.mobile) {
      for (const side of ['left', 'right']) {
        if (this.panels.state[side] !== this.lastPanelState?.[side]) this.restore[side] = this.panels.state[side];
      }
      this.remember();
    }
    this.lastPanelState = { ...this.panels.state };
  }

  /** Сжатая полоса сохраняет название проекта, активный путь и факт изоляции, даже когда управление скрыто. */
  updateContext() {
    const find = id => this.document?.getElementById?.(id);
    const project = find('project-name')?.textContent || 'Карта проекта';
    let view = find('view-title')?.textContent || '';
    const path = find('path-toolbar');
    if (path && !path.hidden) view = path.querySelector('[data-path-toolbar="current"]')?.textContent || view;
    const isolation = find('isolation-toolbar');
    if (isolation && !isolation.hidden) view += ' · Изоляция';
    const limited = path && !path.hidden && path.querySelector('.path-analysis-limited');
    if (limited) view += ' · Анализ ограничен';
    const text = `${project}${view ? ` · ${view}` : ''}`;
    if (this.context.textContent !== text) this.context.textContent = text;
    this.context.title = text;
    this.context.hidden = !this.compact && (this.state.controls || this.state.search);
  }

  /** Canvas занимает свободную высоту окна; размер меняется без fit и пересчёта расположения узлов. */
  updateHeight() {
    if (this.disposed || !this.viewport || !this.canvas.getBoundingClientRect) return;
    const top = Math.max(0, this.canvas.getBoundingClientRect().top, this.toolbar.getBoundingClientRect().bottom);
    const height = `${Math.max(220, Math.floor(this.viewport.innerHeight - top - this.bottomInset))}px`;
    if (this.canvas.style.getPropertyValue('--map-canvas-height') !== height) this.canvas.style.setProperty('--map-canvas-height', height);
  }

  /** Освобождает наблюдатели и обработчики при уничтожении рабочей области. */
  dispose() {
    this.disposed = true;
    for (const [name, callback] of Object.entries(this.callbacks)) this.toggles[name].removeEventListener('click', callback);
    this.sizeObserver?.disconnect();
    this.contentObserver?.disconnect();
    this.viewport?.removeEventListener('resize', this.resize);
    this.viewport?.removeEventListener('scroll', this.resize);
  }
}
