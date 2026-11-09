/** Local engine preferences and bounded checks; no agent connection or engine installation is needed. */
const FIELD_LABELS = {
  enginePath: 'Путь к движку', nodePath: 'Путь к Node.js', pythonPath: 'Путь к Python',
  dotnetPath: 'Путь к .NET', workerPath: 'Путь к Roslyn worker', sdkPath: 'Путь к SDK / references',
  phpPath: 'Путь к PHP',
};
const AVAILABILITY = {
  unchecked: 'Ещё не проверен', checking: 'Проверяется', available: 'Движок найден',
  missing: 'Движок не найден', incompatible: 'Версия или окружение не поддерживаются',
  failed: 'Проверка не завершилась', disabled: 'Выключен',
};
const SOURCE_RULES = ['projectRules', 'buildRules', 'testFrameworks', 'entrypointProviders', 'markupProviders'];

/** Semantic-only changes do not require rebuilding the baseline Graphify/source map. */
export function isSemanticOnlyMod(mod) {
  const analysis = mod.manifest?.analysis;
  if (!analysis?.semanticProviders?.length) return false;
  for (const name of SOURCE_RULES) if (analysis[name]?.length) return false;
  return !analysis.documentProviders?.length;
}

/** Separate tool discovery from applied facts, preserving partial/static evidence. */
export function engineStatusText(provider, persisted = false) {
  const availability = provider.availability || {};
  let text = AVAILABILITY[availability.status] || 'Состояние неизвестно';
  if (availability.engine_version) text += ` · ${availability.engine_version}`;
  if (provider.enrichment === 'applied') text += '. Доступные уточнения применены; анализ неполный.';
  else if (availability.status === 'available') text += '. Уточнения выбранных узлов ещё не применены.';
  else if (availability.status !== 'checking') text += '. Базовая карта доступна.';
  if (persisted) text = 'Сохранённое наблюдение: ' + text;
  return text;
}

/** Own one settings-page controller; reload/dispose fences detached panels and stops their polls. */
export function createSemanticEngineSettings({request, post, document: doc = globalThis.document,
  notify = () => {}, wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)),
  maxPolls = 16, pollMs = 500, checkTimeoutMs = 12000} = {}) {
  let settings = null;
  let projects = [];
  let failure = '';
  let generation = 0;
  const checks = new Set();

  /** Use text nodes for all server/user content, including filesystem paths and project names. */
  function element(tag, text) {
    const node = doc.createElement(tag);
    if (text != null) node.textContent = text;
    return node;
  }

  /** Cancel client waiting only; server work remains bounded by the application's common runtime. */
  function dispose() {
    generation += 1;
    for (const controller of checks) controller.abort();
    checks.clear();
  }

  /** Load preferences/project metadata without searching the machine or starting an analyzer. */
  async function load() {
    dispose();
    const epoch = generation;
    const [preferences, catalog] = await Promise.allSettled([
      request('/api/semantic-engines/settings'), request('/api/projects'),
    ]);
    if (epoch !== generation) return;
    settings = preferences.status === 'fulfilled' ? preferences.value : null;
    failure = preferences.status === 'rejected' ? preferences.reason.message : '';
    projects = [];
    if (catalog.status === 'fulfilled') {
      for (const project of catalog.value.projects || []) {
        if (typeof project.id === 'string' && typeof project.snapshotId === 'string' && project.snapshotId) projects.push(project);
      }
    }
  }

  /** Build an optional per-mod form with explicit save/reset and a selected-map compatibility check. */
  function render(mod) {
    const providers = mod.manifest?.analysis?.semanticProviders;
    if (!providers?.length) return null;
    const panel = element('details');
    panel.className = 'semantic-engine-settings';
    panel.dataset.semanticMod = mod.id;
    const paths = settings?.mods?.[mod.id] || {};
    const explicit = Object.keys(paths).length > 0;
    const summary = element('summary', explicit ? 'Движок: выбранные пути' : 'Движок: автоматический поиск');
    panel.append(summary, element('p', 'Atlas сам ищет подходящий установленный движок при работе с картой. Агент не нужен. Пустые поля сохраняют автоматический выбор.'));
    if (!settings) {
      panel.append(element('p', failure || 'Настройки движков пока недоступны. Базовая карта работает.'));
      return panel;
    }
    const epoch = generation;
    const fields = new Set();
    for (const provider of providers) for (const name of settings.fields?.[provider.strategy] || []) {
      if (FIELD_LABELS[name]) fields.add(name);
    }
    const form = element('form');
    const inputs = new Map();
    const fieldResets = new Map();
    for (const name of fields) {
      const label = element('label', FIELD_LABELS[name]);
      const input = element('input');
      input.type = 'text'; input.value = paths[name] || ''; input.placeholder = 'Автоматически';
      input.maxLength = 4096; input.autocomplete = 'off'; input.spellcheck = false;
      input.dataset.semanticEnginePath = name;
      const automatic = element('button', 'Автоматически'); automatic.type = 'button';
      automatic.dataset.semanticResetField = name;
      automatic.setAttribute('aria-label', `Автоматический выбор: ${FIELD_LABELS[name]}`);
      automatic.title = 'Убрать только этот сохранённый путь; остальные настройки сохранятся';
      automatic.addEventListener('click', () => { void persist(false, name); });
      label.append(input); form.append(label, automatic); inputs.set(name, input); fieldResets.set(name, automatic);
    }
    const save = element('button', 'Сохранить пути'); save.type = 'submit';
    const reset = element('button', 'Автоматический выбор'); reset.type = 'button';
    save.dataset.semanticAction = 'save'; reset.dataset.semanticAction = 'reset';
    form.append(save, reset); panel.append(form);
    const status = element('p'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const label = element('label', 'Проверить совместимость с картой');
    const select = element('select'); select.dataset.semanticProject = mod.id;
    const placeholder = element('option', 'Выберите карту'); placeholder.value = ''; select.append(placeholder);
    for (const project of projects) {
      const option = element('option', project.name || project.label || project.id);
      option.value = project.id; select.append(option);
    }
    select.value = projects.length === 1 ? projects[0].id : '';
    label.append(select); panel.append(label);
    const check = element('button', 'Проверить для карты'); check.type = 'button';
    check.dataset.semanticAction = 'check'; panel.append(check, status);
    const discover = element('button', 'Найти установленные пути'); discover.type = 'button';
    discover.dataset.semanticAction = 'discover';
    const candidates = element('div'); candidates.dataset.semanticCandidates = mod.id;
    const guide = element('a', 'Настройка движков для агента через браузер или MCP');
    guide.href = '/engine-setup.html';
    panel.append(discover, candidates, guide);
    let busy = false;

    /** Preserve Off/no-map guards even when the outer management page re-enables its buttons. */
    function controls(disabled) {
      save.disabled = disabled; reset.disabled = disabled; select.disabled = disabled || !projects.length;
      discover.disabled = disabled;
      for (const button of fieldResets.values()) button.disabled = disabled;
      const unavailable = !select.value || mod.analysisMode === 'off';
      check.dataset.invalid = String(unavailable);
      check.disabled = disabled || unavailable;
    }
    controls(false);
    if (!projects.length) status.textContent = 'Пока нет опубликованных карт для проверки. Пути можно сохранить сейчас.';
    if (mod.analysisMode === 'off') status.textContent = 'Мод выключен. Включите его вручную или верните автоматический режим перед проверкой.';
    select.addEventListener('change', () => controls(busy));

    /** Save only paths; failed writes leave typed values and the prior working preferences intact. */
    async function persist(automatic, field = null) {
      if (busy || epoch !== generation) return;
      busy = true; controls(true);
      try {
        const selected = {};
        if (field) selected[field] = '';
        else if (!automatic) for (const [name, input] of inputs) if (input.value.trim()) selected[name] = input.value.trim();
        const body = field ? {paths: selected, mode: 'patch'} : {paths: selected};
        const response = await post(`/api/mods/${encodeURIComponent(mod.id)}/semantic-engine-settings`, body);
        if (epoch !== generation) return;
        settings.mods[mod.id] = response.paths || {};
        for (const [name, input] of inputs) if (!field || name === field) input.value = settings.mods[mod.id][name] || '';
        summary.textContent = response.mode === 'explicit' ? 'Движок: выбранные пути' : 'Движок: автоматический поиск';
        if (field) status.textContent = `${FIELD_LABELS[field]}: автоматический поиск восстановлен. Остальные сохранённые пути не изменены.`;
        else status.textContent = automatic ? 'Автоматический поиск восстановлен. Базовую карту перестраивать не нужно.' : 'Пути сохранены. Они будут проверены при работе с картой; базовую карту перестраивать не нужно.';
        notify();
      } catch (error) {
        if (epoch === generation) status.textContent = error.message;
      } finally {
        if (epoch === generation) { busy = false; controls(false); }
      }
    }
    form.addEventListener('submit', event => { event.preventDefault(); void persist(false); });
    reset.addEventListener('click', () => { void persist(true); });

    /** Discover presence only; choosing a candidate fills a draft and never writes preferences. */
    async function findPaths() {
      if (busy || epoch !== generation) return;
      busy = true; controls(true);
      const controller = new AbortController(); checks.add(controller);
      const deadline = setTimeout(() => controller.abort(), checkTimeoutMs);
      try {
        status.textContent = 'Ищем файлы в известных местах; движки не запускаются…';
        const query = select.value ? '?' + new URLSearchParams({projectId: select.value}) : '';
        const data = await request(`/api/mods/${encodeURIComponent(mod.id)}/semantic-engine-diagnostics${query}`, {signal: controller.signal});
        if (epoch !== generation || controller.signal.aborted) return;
        candidates.textContent = '';
        let found = 0;
        for (const [name, values] of Object.entries(data.candidates || {})) {
          if (!inputs.has(name) || !Array.isArray(values)) continue;
          for (const path of values.slice(0, 3)) {
            if (typeof path !== 'string') continue;
            const row = element('p');
            const choice = element('button', `Подставить: ${FIELD_LABELS[name]}`); choice.type = 'button';
            choice.dataset.semanticCandidateField = name; choice.title = path;
            choice.addEventListener('click', () => {
              if (busy || epoch !== generation) return;
              inputs.get(name).value = path;
              status.textContent = 'Путь подставлен. Нажмите «Сохранить пути», затем проверьте совместимость с картой.';
            });
            const value = element('code', path); value.style.overflowWrap = 'anywhere';
            row.append(choice, value); candidates.append(row); found += 1;
          }
        }
        status.textContent = found ? 'Найдены существующие файлы. Это не проверка совместимости. Выберите путь и сохраните явно.' : 'В известных местах файлы не найдены. Можно указать пути вручную или оставить автоматический поиск.';
      } catch (error) {
        if (epoch === generation) status.textContent = controller.signal.aborted ? 'Поиск не завершился. Повторите запрос позже.' : error.message;
      } finally {
        clearTimeout(deadline); checks.delete(controller);
        if (epoch === generation) { busy = false; controls(false); }
      }
    }
    discover.addEventListener('click', () => { void findPaths(); });

    /** Inspect one pinned map and stop after a bounded number of read-only status polls. */
    async function inspect() {
      const project = projects.find(item => item.id === select.value);
      if (busy || !project || mod.analysisMode === 'off' || epoch !== generation) return;
      busy = true; controls(true);
      const controller = new AbortController(); checks.add(controller);
      let timedOut = false;
      const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, checkTimeoutMs);
      const url = `/api/projects/${encodeURIComponent(project.id)}/semantic-enrichment`;
      const options = {signal: controller.signal};
      try {
        status.textContent = 'Проверяем установленные движки для выбранной карты…';
        const before = await request(`${url}?${new URLSearchParams({snapshotId: project.snapshotId})}`, options);
        let current = await post(url, {snapshotId: project.snapshotId, nodeIds: [], force: true}, options);
        let fresh = false;
        for (let attempt = 0; attempt < maxPolls; attempt++) {
          if (controller.signal.aborted || epoch !== generation) return;
          await wait(pollMs);
          if (controller.signal.aborted || epoch !== generation) return;
          current = await request(`${url}?${new URLSearchParams({snapshotId: project.snapshotId})}`, options);
          const matching = (current.providers || []).filter(item => item.id?.startsWith(mod.id + '/'));
          const pending = matching.some(item => item.availability?.status === 'checking') || ['queued', 'running'].includes(current.enrichment);
          fresh = current.updatedAt != null && current.updatedAt !== before.updatedAt;
          if (fresh && matching.length && !pending) break;
        }
        if (controller.signal.aborted || epoch !== generation) return;
        const matching = (current.providers || []).filter(item => item.id?.startsWith(mod.id + '/'));
        const messages = matching.map(item => engineStatusText(item, current.persistedObservation));
        status.textContent = messages.join(' ') || 'Для выбранной карты этот мод не выбран автоматически. Проверьте язык карты и режим мода.';
        if (!fresh) status.textContent += ' Новая проверка ещё не завершилась; перечитайте статус позже.';
      } catch (error) {
        if (epoch === generation) {
          if (timedOut) status.textContent = 'Проверка ещё не завершилась. Базовая карта доступна; повторите запрос позже.';
          else if (!controller.signal.aborted) status.textContent = error.message;
        }
      } finally {
        clearTimeout(deadline);
        checks.delete(controller);
        if (epoch === generation) {
          if (timedOut) status.textContent = 'Проверка ещё не завершилась. Базовая карта доступна; повторите запрос позже.';
          busy = false; controls(false);
        }
      }
    }
    check.addEventListener('click', () => { void inspect(); });
    return panel;
  }
  return {load, render, dispose};
}
