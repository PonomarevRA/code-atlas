// Геометрия сцены остаётся полной. Из DOM убираются только невидимые SVG-элементы.
const windows = new WeakMap();
const OVERSCAN = 192;

/** Возвращает видимую область в координатах карты; запас задан в экранных пикселях. */
export function cameraWindow(camera, width, height, overscan = OVERSCAN) {
  if (![camera.x, camera.y, camera.scale, width, height, overscan].every(Number.isFinite)
      || !(camera.scale > 0) || !(width > 0) || !(height > 0)) return null;
  return { x: (-camera.x - overscan) / camera.scale, y: (-camera.y - overscan) / camera.scale,
    width: (width + overscan * 2) / camera.scale, height: (height + overscan * 2) / camera.scale };
}

/** Пересечение включает касание границ и нулевую толщину горизонтальных/вертикальных линий. */
export function intersectsWindow(box, window) {
  return box.x <= window.x + window.width && box.x + box.width >= window.x
    && box.y <= window.y + window.height && box.y + box.height >= window.y;
}

/** Измеряет всю кривую/карточку один раз, включая её собственный transform и декораторы. */
function sceneBounds(element, viewport) {
  if (element.tagName?.toLowerCase() === 'defs' || !element.getBBox) return null;
  try {
    const box = element.getBBox();
    const root = viewport.getCTM(), local = element.getCTM();
    if (!root || !local) return null;
    const matrix = root.inverse().multiply(local);
    const corners = [];
    for (const x of [box.x, box.x + box.width]) for (const y of [box.y, box.y + box.height]) {
      corners.push({ x: matrix.a * x + matrix.c * y + matrix.e, y: matrix.b * x + matrix.d * y + matrix.f });
    }
    const xs = corners.map(point => point.x), ys = corners.map(point => point.y);
    if (![...xs, ...ys].every(Number.isFinite)) return null;
    // getBBox не включает толщину штриха и маркеры стрелок.
    return { x: Math.min(...xs) - 12, y: Math.min(...ys) - 12,
      width: Math.max(...xs) - Math.min(...xs) + 24, height: Math.max(...ys) - Math.min(...ys) + 24 };
  } catch { return null; } // Неизвестная геометрия остаётся видимой, а не теряется.
}

/** Сохраняет SVG identity и обработчики, монтируя только пересекающие окно элементы. */
export class SvgViewport {
  constructor(view) {
    this.view = view;
    this.entries = [];
    this.known = new WeakSet();
    this.fullScene = false;
    windows.set(view.svg, this);
  }

  /** Регистрирует новые слои; уже измеренные элементы не вызывают layout при панорамировании. */
  capture() {
    for (const element of this.view.viewport.children) {
      if (this.known.has(element)) continue;
      this.known.add(element);
      const measurable = this.view.svg.clientWidth > 0 && this.view.svg.clientHeight > 0;
      this.entries.push({ element, bounds: measurable ? sceneBounds(element, this.view.viewport) : null });
    }
  }

  /** Сохраняет порядок слоёв и клавиатурный фокус; камера и модель сцены не изменяются. */
  update() {
    this.capture();
    const view = this.view, viewport = view.viewport;
    const area = cameraWindow(view.camera, view.svg.clientWidth, view.svg.clientHeight);
    const active = viewport.ownerDocument?.activeElement;
    const showAll = this.fullScene || !area || Boolean(view.expansion?.frame);
    let next = null, mounted = 0;
    for (let index = this.entries.length - 1; index >= 0; index--) {
      const { element, bounds } = this.entries[index];
      const focused = active && element.contains?.(active);
      if (showAll || !bounds || focused || intersectsWindow(bounds, area)) {
        if (element.parentNode !== viewport || element.nextSibling !== next) viewport.insertBefore(element, next);
        next = element;
        mounted++;
      } else if (element.parentNode === viewport) element.remove();
    }
    view.svg.setAttribute('data-windowed-elements', mounted);
    view.svg.setAttribute('data-scene-elements', this.entries.length);
  }

  /** Возвращает все элементы перед сменой сцены/контекстных слоёв и освобождает прежний индекс. */
  release() {
    const previous = this.fullScene;
    this.fullScene = true;
    try { this.update(); }
    finally { this.fullScene = previous; }
    this.entries = [];
    this.known = new WeakSet();
  }

  /** Перемеряет изменённые кривые после переключения маршрутизации; обычное движение камеры этого не делает. */
  refreshBounds() {
    this.withFullScene(() => {
      for (const entry of this.entries) entry.bounds = sceneBounds(entry.element, this.view.viewport);
    });
  }

  /** На время синхронного экспорта подключает полную сцену; finally восстанавливает окно при ошибке. */
  withFullScene(callback) {
    const previous = this.fullScene;
    this.fullScene = true;
    this.update();
    try { return callback(); }
    finally { this.fullScene = previous; this.update(); }
  }
}

/** Очищает и экранный слой, и удержанные вне экрана элементы предыдущей сцены. */
export function resetSvgViewport(view) {
  const window = view.viewportWindow;
  if (window) {
    window.entries = [];
    window.known = new WeakSet();
    // Все renderer заканчивают синхронно; измеряем после добавления последних слоёв.
    queueMicrotask(() => window.update());
  }
  view.viewport.replaceChildren();
}

/** Экспорт использует все раскрытые элементы, включая убранные за экран, без изменения камеры. */
export function withFullSvgScene(svg, callback) {
  const window = windows.get(svg);
  return window ? window.withFullScene(callback) : callback();
}
