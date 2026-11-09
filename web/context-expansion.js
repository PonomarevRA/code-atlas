const NS = 'http://www.w3.org/2000/svg';
/** Раскрывает окружение в основной области, сохраняя кликабельный обзор в закреплённой мини-карте. */
export class ContextExpansion {
  /** Получает фабрику вложенного renderer с теми же callbacks и провайдером. */
  constructor(view, createChild) { this.view = view; this.createChild = createChild; }
  /** Переносит существующий SVG в контекстный слой без пересоздания исходных элементов. */
  open(payload) {
    this.close(true);
    const view = this.view;
    // Мини-карта переносит полную исходную сцену, включая элементы за экраном.
    view.viewportWindow?.release();
    this.camera = { ...view.camera };
    const baseBounds = view.geometryBounds();
    this.baseNodeElements = view.nodeElements;
    const context = document.createElementNS(NS, 'g');
    context.setAttribute('class', 'expansion-context');
    while (view.viewport.firstChild) context.append(view.viewport.firstChild);
    view.viewport.append(context);
    const local = document.createElementNS(NS, 'g');
    local.setAttribute('class', 'expansion-local');
    view.viewport.append(local);
    const child = this.createChild(local);
    child.render(payload.nodes, payload.edges, payload.selectedId);
    const bounds = child.geometryBounds();
    // Локальные символы занимают основную область; закреплённый обзор не участвует в мировом fit.
    local.setAttribute('transform', 'translate(0,0)');
    this.localBounds = bounds;
    this.selectedId = payload.selectedId;
    this.bounds = { ...bounds };
    const frame = document.createElementNS(NS, 'g');
    frame.setAttribute('class', 'expansion-minimap');
    const background = document.createElementNS(NS, 'rect');
    for (const [key, value] of Object.entries({ width: 170, height: 132, rx: 7, fill: '#fbfcfa', stroke: '#92a99b' })) background.setAttribute(key, value);
    const title = document.createElementNS(NS, 'text');
    title.setAttribute('x', 10); title.setAttribute('y', 17); title.setAttribute('font-size', 12);
    title.textContent = 'Общая карта';
    frame.append(background, title, context);
    view.viewport.append(frame);
    this.frame = frame;
    this.baseBounds = baseBounds;
    this.context = context;
    this.local = local;
    this.child = child;
    view.nodeElements = new Map([...this.baseNodeElements, ...child.nodeElements]);
    this.updateContextTransform();
  }
  /** Закрепляет кликабельную общую карту в углу экрана при масштабе локальных символов. */
  updateContextTransform() {
    if (!this.frame) return;
    const camera = this.view.camera;
    const bounds = this.baseBounds;
    const scale = Math.min(150 / Math.max(1, bounds.maxX - bounds.minX), 100 / Math.max(1, bounds.maxY - bounds.minY));
    this.context.setAttribute('transform', `translate(${10 - bounds.minX * scale},${26 - bounds.minY * scale}) scale(${scale})`);
    this.frame.setAttribute('transform', `translate(${(10 - camera.x) / camera.scale},${(10 - camera.y) / camera.scale}) scale(${1 / camera.scale})`);
  }
  /** Приближает выбранный символ до читаемого масштаба; окружение можно вместить отдельной кнопкой. */
  focus() {
    const point = this.child?.positions.get(this.selectedId);
    if (!point) return;
    const scale = this.view.displayProvider === 'flow' ? .75 : 1;
    this.view.camera = { x: this.view.svg.clientWidth * .62 - point.x * scale, y: this.view.svg.clientHeight * .46 - point.y * scale, scale };
    this.view.transform();
  }
  /** Вмещает только развёрнутые символы, не заставляя читать их в масштабе общей карты. */
  fitLocal() {
    if (!this.localBounds) return;
    const view = this.view;
    const bounds = this.localBounds;
    const width = view.svg.clientWidth, height = view.svg.clientHeight;
    const scale = Math.min(1.6, (width - 60) / (bounds.maxX - bounds.minX), (height - 100) / (bounds.maxY - bounds.minY));
    view.camera = { x: width / 2 - (bounds.minX + bounds.maxX) / 2 * scale, y: height / 2 - (bounds.minY + bounds.maxY) / 2 * scale, scale };
    view.transform();
  }
  /** Возвращает те же элементы, координаты и исходную камеру; факты графа не изменяются. */
  close(restoreCamera = true) {
    if (!this.context) return;
    const view = this.view;
    view.viewportWindow?.release();
    this.local.remove();
    while (this.context.firstChild) view.viewport.append(this.context.firstChild);
    this.context.remove();
    this.frame?.remove();
    this.frame = null;
    view.nodeElements = this.baseNodeElements;
    this.context = null;
    this.child = null;
    this.bounds = null;
    if (restoreCamera) { view.camera = this.camera; view.transform(); }
  }
}
