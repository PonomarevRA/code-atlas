/** Opt-in structural experiments; the host owns source, membership and context expiry. */
import {requestJson} from './bounded-request.js';
import {moduleLayoutContinuity} from './module-layout-continuity.js';

/** Render plain graph labels without HTML interpretation. */
function node(document, tag, text) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = String(text);
  return result;
}

/**
 * Connect bounded proposals to the existing page's explicit project/snapshot scope.
 * @param {HTMLElement} root Experiment section with owned action/status/result placeholders.
 * @param {HTMLFormElement} form Existing class/folder scope form; its source state remains host-owned.
 * @param {object} options Optional native-fetch-compatible transport for isolated tests.
 */
export class ModuleProposalsPanel {
  constructor(root, form, {fetcher = (...args) => fetch(...args)} = {}) {
    this.root = root; this.form = form; this.document = root.ownerDocument; this.fetcher = fetcher;
    this.status = root.querySelector('[data-proposal-status]');
    this.output = root.querySelector('[data-proposal-output]');
    this.buttons = [...root.querySelectorAll('[data-proposal-action]')];
    this.generation = 0; this.reference = null; this.positions = new Map(); this.pins = new Set();
    this.frame = null; this.controller = null; this.disposed = false;
    this.click = event => {
      const button = event.target.closest?.('[data-proposal-action]');
      if (button && root.contains(button)) this.run(button.dataset.proposalAction);
    };
    this.changed = () => this.reset();
    this.parametersChanged = event => {
      if (event.target.matches?.('[data-proposal-seed], [data-proposal-budget]')) this.reset();
    };
    root.addEventListener('click', this.click);
    root.addEventListener('input', this.parametersChanged); root.addEventListener('change', this.parametersChanged);
    form.addEventListener('input', this.changed);
    form.addEventListener('change', this.changed);
  }

  /**
   * Read only the visible scope. A newer snapshot may be supplied for the explicit update action.
   * @returns {object} Project plus validated-by-server scope; this method never rebuilds a map.
   */
  scope(snapshotId = null) {
    const projectId = this.form.querySelector('#project').value;
    const snapshot = snapshotId || this.form.querySelector('#snapshot').value;
    if (!projectId || !snapshot) throw new Error('Выберите проект с готовым снимком.');
    const level = this.form.querySelector('#level').value;
    const folders = this.form.querySelector('#folders').value.split('\n').map(value => value.trim()).filter(Boolean);
    return {projectId, body: {snapshotId: snapshot, level, folders: level === 'folder' && folders.length ? folders : null}};
  }

  /** Execute one bounded request; failures never become empty successful proposals. */
  async request(path, body, signal) {
    const options = {signal};
    if (body !== undefined) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return requestJson(path, options, {fetcher: this.fetcher, timeoutMs: 30000, activity: false});
  }

  /** Release the selected old residency scope; a changed project never selects another handle. */
  async release(reference) {
    if (!reference) return;
    try {
      await this.request('/api/projects/'+encodeURIComponent(reference.projectId)+'/analysis-contexts/'+
        encodeURIComponent(reference.state.contextId)+'/close', {snapshotId: reference.state.snapshotId});
    } catch { /* Expired or already-closed analysis residency requires no automatic retry. */ }
  }

  /** Invalidate ignored-abort responses and retire a no-longer-relevant private baseline. */
  reset() {
    this.generation += 1; this.controller?.abort(); this.controller = null;
    const old = this.reference; this.reference = null; this.release(old);
    this.positions = new Map(); this.pins.clear(); this.frame = null;
    this.output.replaceChildren(); this.status.textContent = 'Область изменена. Подготовьте новое сравнение.';
    this.buttons.forEach(button => { button.disabled = false; });
    this.output.removeAttribute('aria-busy');
  }

  /**
   * Run search, baseline preparation or update on an explicit captured revision.
   * @param {string} action search, baseline or update; no action applies a partition to a saved map.
   */
  async run(action) {
    if (this.disposed || !['search', 'baseline', 'update'].includes(action)) return;
    const revision = ++this.generation;
    this.controller?.abort(); const controller = new AbortController(); this.controller = controller;
    this.buttons.forEach(button => { button.disabled = true; }); this.output.setAttribute('aria-busy', 'true');
    this.status.textContent = 'Вычисляем предложение…';
    let newlyReturned = null;
    try {
      let selected = this.scope();
      if (action !== 'search') {
        const catalog = await this.request('/api/projects', undefined, controller.signal);
        if (revision !== this.generation) return;
        const latest = catalog.projects.find(item => item.id === selected.projectId);
        if (!latest?.snapshotId) throw new Error('Текущий снимок недоступен.');
        selected = this.scope(latest.snapshotId);
      }
      const {projectId, body} = selected;
      let operation = 'module-search';
      if (action === 'search') {
        body.seed = Number(this.root.querySelector('[data-proposal-seed]').value);
        body.evaluationsPerMethod = Number(this.root.querySelector('[data-proposal-budget]').value);
        body.maxProposals = 8;
      } else {
        operation = 'module-updates'; body.maxEvaluations = 4096;
        if (action === 'update') {
          if (!this.reference || this.reference.projectId !== projectId) throw new Error('Сначала сохраните базу в этой области.');
          body.stateRef = this.reference.state;
        }
      }
      const report = await this.request('/api/projects/'+encodeURIComponent(projectId)+'/'+operation, body, controller.signal);
      if (report.stateRef) newlyReturned = {projectId, state: report.stateRef};
      if (revision !== this.generation || this.disposed) { await this.release(newlyReturned); return; }
      if (report.projectId !== projectId || report.snapshotId !== body.snapshotId) throw new Error('Область ответа не совпала с запросом.');
      if (action === 'search') this.renderSearch(report);
      else {
        const previous = this.reference;
        this.renderUpdate(report);
        this.reference = newlyReturned; newlyReturned = null;
        await this.release(previous);
      }
      if (revision !== this.generation || this.disposed) return;
      this.status.textContent = report.search?.status === 'cancelled'
        ? 'Достигнут серверный бюджет: показаны только полностью оценённые варианты.'
        : 'Готово. Предложение не изменяет карту или исходники.';
    } catch (error) {
      await this.release(newlyReturned);
      if (revision === this.generation && !this.disposed) this.status.textContent = error.name === 'AbortError'
        ? 'Ожидание прекращено. Сервер ограничивает вычисление своим бюджетом.' : error.message;
    } finally {
      if (revision === this.generation && !this.disposed) {
        this.controller = null; this.buttons.forEach(button => { button.disabled = false; });
        this.output.removeAttribute('aria-busy');
      }
    }
  }

  /** Map stable unit IDs to readable labels; missing labels retain their exact identity. */
  names(report) {
    return new Map(report.projection.units.map(unit => [unit.id,
      unit.label + (unit.files.length ? ' · '+unit.files.join(', ') : '')]));
  }

  /** Render whole candidate partitions and explicit objective/baseline limits. */
  renderSearch(report) {
    this.output.replaceChildren(); const names = this.names(report);
    this.output.append(node(this.document, 'h3', 'Варианты структуры'),
      node(this.document, 'p', `Снимок ${report.snapshotId}; покрытие: ${report.projection.coverage.status}.`));
    for (const proposal of report.search.proposals) {
      const details = node(this.document, 'details');
      details.append(node(this.document, 'summary', `MQ ${proposal.objectives.atlasIncidentMQ.toFixed(3)} · изменено модулей: ${proposal.objectives.movedUnits}`));
      const list = node(this.document, 'ol');
      for (const group of proposal.partition) list.append(node(this.document, 'li', group.map(id => names.get(id) || id).join(', ')));
      details.append(list); this.output.append(details);
    }
    const evidence = node(this.document, 'details');
    evidence.append(node(this.document, 'summary', 'Цели, бюджеты и ограничения'),
      node(this.document, 'pre', JSON.stringify({objectives: report.search.objectiveDefinitions,
        budgets: report.search.budgets, methods: report.search.methods, baselines: report.search.baselines,
        limitations: report.search.limitations, truncated: report.search.truncated}, null, 2)));
    this.output.append(evidence);
  }

  /** Render membership separately from geometry, reusing the existing stable layout facade. */
  renderUpdate(report) {
    this.output.replaceChildren(); const names = this.names(report);
    this.output.append(node(this.document, 'h3', 'Обновление групп'), node(this.document, 'p',
      `Снимок ${report.snapshotId}; изменено пар совместного членства: ${report.update.membership.changedPairs}.`));
    const list = node(this.document, 'ol');
    for (const group of report.update.groups) list.append(node(this.document, 'li', group.map(id => names.get(id) || id).join(', ')));
    this.output.append(list);
    const frame = report.projectId+':'+report.update.profileHash;
    const current = new Map();
    report.projection.units.forEach((unit, index) => current.set(unit.id,
      {x: index%4*300, y: Math.floor(index/4)*150, width: 250, height: 80, labelWidth: Math.min(1000, names.get(unit.id).length*8)}));
    const geometry = moduleLayoutContinuity(current, this.positions,
      {frameId: frame, previousFrameId: this.frame, pinnedIds: this.pins});
    this.positions = geometry.positions; this.frame = frame;
    const preview = node(this.document, 'div'); preview.className = 'module-geometry-scroll';
    const svg = this.document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Отдельный предпросмотр положения модулей');
    const extents = [...this.positions.values()];
    const left = Math.min(0, ...extents.map(point => point.x-point.width/2))-20;
    const top = Math.min(0, ...extents.map(point => point.y-point.height/2))-20;
    const width = Math.max(300, ...extents.map(point => point.x+point.width/2))-left+20;
    const height = Math.max(120, ...extents.map(point => point.y+point.height/2))-top+20;
    svg.setAttribute('viewBox', `${left} ${top} ${width} ${height}`);
    svg.setAttribute('width', width); svg.setAttribute('height', height);
    const boxes = new Map();
    for (const [id, point] of this.positions) {
      const rect = this.document.createElementNS(svg.namespaceURI, 'rect');
      rect.setAttribute('x', point.x-point.width/2); rect.setAttribute('y', point.y-point.height/2);
      rect.setAttribute('width', point.width); rect.setAttribute('height', point.height);
      rect.setAttribute('fill', 'var(--panel-bg, white)'); rect.setAttribute('stroke', 'currentColor');
      rect.setAttribute('stroke-width', this.pins.has(id) ? 4 : 1); boxes.set(id, rect);
      const title = this.document.createElementNS(svg.namespaceURI, 'title'); title.textContent = names.get(id);
      rect.append(title); svg.append(rect);
      const text = this.document.createElementNS(svg.namespaceURI, 'text');
      text.setAttribute('x', point.x); text.setAttribute('y', point.y+4); text.setAttribute('text-anchor', 'middle');
      text.setAttribute('fill', 'currentColor'); text.setAttribute('font-size', 12);
      const label = names.get(id) || id; text.textContent = label.length > 116 ? label.slice(0, 113)+'…' : label;
      svg.append(text);
    }
    preview.append(svg); this.output.append(node(this.document, 'p',
      'Предпросмотр положения этой страницы. Полные имена и состав групп приведены выше; сохранённая карта не изменяется.'), preview);
    const controls = node(this.document, 'details');
    controls.append(node(this.document, 'summary', 'Положение модулей · закрепления'));
    for (const unit of report.projection.units) {
      const label = node(this.document, 'label'); const box = node(this.document, 'input'); box.type = 'checkbox';
      box.checked = this.pins.has(unit.id); box.setAttribute('aria-label', 'Закрепить положение: '+names.get(unit.id));
      box.addEventListener('change', () => {
        if (box.checked) this.pins.add(unit.id); else this.pins.delete(unit.id);
        boxes.get(unit.id)?.setAttribute('stroke-width', box.checked ? 4 : 1);
      });
      label.append(box, node(this.document, 'span', names.get(unit.id))); controls.append(label, node(this.document, 'br'));
    }
    controls.append(node(this.document, 'p', 'Закрепления относятся к геометрии этой страницы. Состав групп ими не меняется.'));
    this.output.append(controls);
    const evidence = node(this.document, 'details');
    evidence.append(node(this.document, 'summary', 'Ревизии, Q, иерархия и положение'), node(this.document, 'pre', JSON.stringify({
      algorithm: report.algorithm, coverage: report.projection.coverage, delta: report.update.delta,
      objective: report.update.objective, search: report.update.search, hierarchy: report.update.hierarchy,
      membership: report.update.membership, geometry: geometry.report}, null, 2)));
    this.output.append(evidence);
  }

  /** Release owned handlers/context; no hidden timer keeps the analysis resident. */
  dispose() {
    if (this.disposed) return;
    this.reset(); this.disposed = true;
    this.root.removeEventListener('click', this.click);
    this.root.removeEventListener('input', this.parametersChanged); this.root.removeEventListener('change', this.parametersChanged);
    this.form.removeEventListener('input', this.changed); this.form.removeEventListener('change', this.changed);
  }
}
