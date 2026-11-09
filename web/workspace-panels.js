const STORAGE_KEY = 'atlas-workspace-panels-v1';
const SIDES = ['left', 'right'];

/** Видимость боковых панелей: без карты, фильтров, загрузки данных и Browser History. */
export class WorkspacePanels {
  /**
   * Настраивает desktop/mobile отдельно; mobile допускает не более одной открытой панели.
   * @param {object} options workspace/panels/toggles — реальные узлы; storage/media/onLayout внедряются хостом.
   * bindToggles включает собственные click listeners; defaultOpen задаёт только начальный desktop-вариант.
   * Сохранённые boolean-предпочтения имеют приоритет над defaultOpen; явный выбор пользователя не стирается.
   * @throws {TypeError} Если workspace или одна из пар panel/toggle не переданы.
   */
  constructor({ workspace, panels, toggles, storage, media, onLayout = () => {}, bindToggles = true, defaultOpen = {left:true,right:true} }) {
    if (!workspace || SIDES.some(side => !panels?.[side] || !toggles?.[side])) {
      throw new TypeError('WorkspacePanels требует workspace, две панели и две внешние кнопки.');
    }
    this.workspace = workspace;
    this.panels = panels;
    this.toggles = toggles;
    this.onLayout = onLayout;
    this.bindToggles = bindToggles;
    this.focusTargets = { ...toggles };
    this.document = workspace.ownerDocument || globalThis.document;
    try { this.storage = storage === undefined ? globalThis.localStorage : storage; }
    catch { this.storage = null; }
    this.media = typeof media === 'function' ? media('(max-width: 900px)') : media || globalThis.matchMedia?.('(max-width: 900px)');
    this.preferences = { desktop: { left: defaultOpen.left!==false, right: defaultOpen.right!==false }, mobile: { left: false, right: false } };
    this.readPreferences();
    this.savedPreferences = { desktop: { ...this.preferences.desktop }, mobile: { ...this.preferences.mobile } };
    this.clicks = {};
    for (const side of SIDES) {
      this.clicks[side] = () => this.toggle(side);
      if (bindToggles) toggles[side].addEventListener('click', this.clicks[side]);
      const id = panels[side].id || panels[side].getAttribute?.('id');
      if (id) toggles[side].setAttribute('aria-controls', id);
    }
    this.mediaChange = () => this.apply('viewport');
    this.escape = event => {
      if (!this.mobile || event.key !== 'Escape' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      const inside = !event.target || event.target === this.document?.body || event.target === this.document?.documentElement
        || workspace.contains(event.target) || SIDES.some(side => toggles[side].contains(event.target));
      if (!inside) return;
      const side = SIDES.find(name => this.state[name]);
      if (side) { event.preventDefault(); this.setOpen(side, false); this.focusTargets[side].focus?.({ preventScroll: true }); }
    };
    this.document?.addEventListener('keydown', this.escape);
    if (this.media?.addEventListener) this.media.addEventListener('change', this.mediaChange);
    else this.media?.addListener?.(this.mediaChange);
    this.observeToolbar();
    this.apply('initial');
  }

  /** Mobile drawers начинаются под фактической toolbar, включая перенос строк и sticky-прокрутку. */
  observeToolbar() {
    const toolbar = this.document?.querySelector?.('.workspace-controls');
    const viewport = this.document?.defaultView;
    if (!toolbar || !viewport) return;
    this.viewport = viewport;
    this.toolbarAnchor = () => {
      if (this.disposed) return;
      const bottom = Math.max(0, Math.ceil(toolbar.getBoundingClientRect().bottom));
      this.document.documentElement.style.setProperty('--workspace-toolbar-bottom', `${bottom}px`);
    };
    viewport.addEventListener('resize', this.toolbarAnchor, { passive: true });
    viewport.addEventListener('scroll', this.toolbarAnchor, { passive: true });
    if (viewport.ResizeObserver) {
      this.toolbarObserver = new viewport.ResizeObserver(this.toolbarAnchor);
      this.toolbarObserver.observe(toolbar);
    }
    this.toolbarAnchor();
  }

  /** Возвращает текущий режим и независимые предпочтения этого размера экрана. */
  get mobile() { return Boolean(this.media?.matches); }
  get state() { return this.preferences[this.mobile ? 'mobile' : 'desktop']; }

  /** Принимает только короткий JSON с четырьмя boolean; повреждённое хранилище не мешает карте. */
  readPreferences() {
    try {
      const text = this.storage?.getItem(STORAGE_KEY);
      if (!text || text.length > 512) return;
      const saved = JSON.parse(text);
      for (const mode of ['desktop', 'mobile']) {
        if (!saved?.[mode] || SIDES.some(side => typeof saved[mode][side] !== 'boolean')) continue;
        if (mode === 'mobile' && saved[mode].left && saved[mode].right) continue;
        this.preferences[mode] = { left: saved[mode].left, right: saved[mode].right };
      }
    } catch { /* В приватном режиме панели работают без сохранения предпочтений. */ }
  }

  /** Сохраняет явное действие, не превращая временный reveal другой панели в предпочтение. */
  remember(side) {
    if (this.mobile) this.savedPreferences.mobile = { ...this.state };
    else this.savedPreferences.desktop[side] = this.state[side];
    try { this.storage?.setItem(STORAGE_KEY, JSON.stringify(this.savedPreferences)); }
    catch { /* Неудачная запись не отменяет действие пользователя. */ }
  }

  /** Согласует hidden/inert/ARIA и возвращает фокус, если скрывается его текущая панель. */
  apply(reason) {
    for (const side of SIDES) {
      const panel = this.panels[side];
      const open = this.state[side];
      if (!open && panel.contains(this.document?.activeElement)) this.focusTargets[side].focus?.({ preventScroll: true });
      panel.hidden = !open;
      panel.inert = !open;
      panel.setAttribute('aria-hidden', String(!open));
      this.toggles[side].setAttribute('aria-expanded', String(open));
      this.workspace.classList.toggle(`panels-${side}-collapsed`, !open);
    }
    this.onLayout({ ...this.state, mobile: this.mobile, reason });
  }

  /** Меняет видимость без изменения содержимого; focus=true переводит фокус в открытую панель. */
  setOpen(side, open, { focus = false, remember = true } = {}) {
    if (!SIDES.includes(side) || typeof open !== 'boolean' || this.disposed) return false;
    const changed = this.state[side] !== open;
    if (this.mobile && open) this.state[side === 'left' ? 'right' : 'left'] = false;
    this.state[side] = open;
    if (changed) this.apply(remember ? 'action' : 'reveal');
    if (remember && changed) this.remember(side);
    if (open && focus) {
      const panel = this.panels[side];
      const control = panel.querySelector('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]');
      if (control) control.focus({ preventScroll: true });
      else { panel.setAttribute('tabindex', '-1'); panel.focus?.({ preventScroll: true }); }
    }
    return changed;
  }

  /** Native button click также обслуживает Enter/Space; отдельный keydown не удваивает действие. */
  toggle(side) { return this.setOpen(side, !this.state[side]); }

  /** Открывает нужную панель для явного действия «Описание»/«Исходник», не забирая фокус. */
  reveal(side) { return this.setOpen(side, true, { remember: false }); }

  /** Удаляет подписки при уничтожении workspace; последующие действия безопасно игнорируются. */
  dispose() {
    this.disposed = true;
    if (this.bindToggles) for (const side of SIDES) this.toggles[side].removeEventListener('click', this.clicks[side]);
    this.document?.removeEventListener('keydown', this.escape);
    if (this.media?.removeEventListener) this.media.removeEventListener('change', this.mediaChange);
    else this.media?.removeListener?.(this.mediaChange);
    this.toolbarObserver?.disconnect();
    this.viewport?.removeEventListener('resize', this.toolbarAnchor);
    this.viewport?.removeEventListener('scroll', this.toolbarAnchor);
    if (this.toolbarAnchor) this.document.documentElement.style.removeProperty('--workspace-toolbar-bottom');
  }
}
