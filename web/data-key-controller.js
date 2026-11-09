/** Выбор поля и изоляция значения работают только с опубликованным снимком. */
export class DataKeyExplorer {
  /** Контроллер владеет выбором и пагинацией; API и инспектор предоставляет страница. */
  constructor(controls, scene, query, showRows, restore, report) {
    this.controls = controls; this.scene = scene; this.query = query;
    this.showRows = showRows; this.restore = restore; this.report = report; this.revision = 0;
    controls.select.addEventListener('change', () => this.run(() => this.chooseKey()));
    controls.moreKeys.addEventListener('click', () => this.run(() => this.loadKeys(this.keyCursor)));
    controls.moreValues.addEventListener('click', () => this.run(() => this.loadValues(this.valueCursor)));
    controls.isolate.addEventListener('click', () => { this.isolated = !this.isolated; this.draw(); });
    controls.moreRows.addEventListener('click', () => this.run(() => this.chooseValue(this.value, this.path.nextCursor)));
    this.reset();
  }
  /** Ошибки запросов остаются видимыми, включая SQL-бюджет больших карт. */
  async run(callback) { try { await callback(); } catch (error) { this.report(error); } }
  /** Поздние ответы предыдущей карты/поля не меняют текущую сцену. */
  reset() {
    this.revision++; this.key = null; this.value = null; this.values = []; this.path = null; this.isolated = false;
    this.keyCursor = null; this.valueCursor = null;
    this.controls.select.replaceChildren(new Option('Структура файлов', ''));
    this.controls.bar.hidden = true;
    for (const name of ['moreKeys', 'moreValues', 'isolate', 'moreRows']) this.controls[name].hidden = true;
    this.controls.info.textContent = '';
  }
  /** Дополняет список полей в серверном порядке: самые частые совпадения сверху. */
  async loadKeys(cursor = null) {
    const revision = this.revision;
    this.controls.bar.hidden = false;
    const result = await this.query('keys', { limit: 50, cursor });
    if (revision !== this.revision) return;
    for (const key of result.items) {
      const option = new Option(`${key.field} · ${key.file} · повторений ${key.repeatOccurrences}, совпадений ${key.matchedRecords}`, key.id);
      this.controls.select.append(option);
    }
    this.keyCursor = result.nextCursor; this.controls.moreKeys.hidden = !this.keyCursor;
  }
  /** Выбор поля раскрывает одну колонку значений; пустой выбор возвращает структуру. */
  async chooseKey() {
    this.revision++; this.values = []; this.path = null; this.value = null; this.isolated = false;
    this.showRows(null);
    this.key = this.controls.select.value;
    this.controls.moreRows.hidden = true; this.controls.isolate.hidden = true; this.controls.moreValues.hidden = true;
    if (!this.key) { this.controls.info.textContent = ''; await this.restore(); return; }
    await this.loadValues();
  }
  /** Страница заменяет значения, чтобы граф не рос вместе с числом строк файла. */
  async loadValues(cursor = null) {
    const revision = ++this.revision, keyId = this.key;
    const result = await this.query('key-values', { keyId, limit: 20, cursor });
    if (revision !== this.revision) return;
    this.keyNode = result.key; this.values = result.items; this.path = null; this.value = null; this.isolated = false;
    this.showRows(null);
    this.valueCursor = result.nextCursor; this.controls.moreValues.hidden = !this.valueCursor;
    this.draw();
  }
  /** Выбранное значение показывает строки и далее только достижимые связи. */
  async chooseValue(value, cursor = null) {
    const revision = ++this.revision;
    const result = await this.query('key-path', { keyId: this.key, valueRecordId: value.valueRecordId,
      limit: 20, maxNodes: 60, maxDepth: 2, cursor });
    if (revision !== this.revision) return;
    this.value = value; this.path = result; this.draw(); this.showRows(result);
  }
  /** Разводит ключ, значения и записи по колонкам; изоляция убирает остальные значения. */
  draw() {
    const nodes = new Map(), links = [];
    if (!this.isolated) {
      nodes.set(this.keyNode.id, this.keyNode);
      for (const value of this.values) nodes.set(value.id, value);
    }
    if (this.path) {
      for (const node of this.path.nodes) nodes.set(node.id, node);
      // Связь поле → значение уже задаётся owner: не рисуем её второй раз.
      links.push(...this.path.links.filter(link => link.relation !== 'field_value'));
    }
    this.scene.setGraph([...nodes.values()], links);
    this.controls.isolate.hidden = !this.path;
    this.controls.isolate.textContent = this.isolated ? 'Показать остальные значения' : 'Изолировать путь';
    this.controls.isolate.setAttribute('aria-pressed', String(this.isolated));
    this.controls.moreRows.hidden = !this.path?.nextCursor;
    this.controls.info.textContent = this.path
      ? `${this.isolated ? 'Изолирован путь' : 'Выбрано значение'}: ${this.value.label} (${this.value.valueType}) · строк: ${this.path.matchedRecords}${this.path.truncated ? ' · показана часть связей' : ''}`
      : 'Ключ → значения по частоте → строки → связи. Нажмите на значение.';
  }
  /** Перехватывает карточки нового вида, оставляя сценарии и структуру странице. */
  async select(node) {
    if (node.kind === 'data-value') { await this.chooseValue(node); return true; }
    if (node.kind === 'data-key') { await this.loadValues(); return true; }
    return false;
  }
}
