import {architectureExport} from './architecture-export.js';
import { withFullSvgScene } from './svg-viewport.js';
import { createExportNotice } from './export-notice.js';
import { requestJson } from './bounded-request.js';
const MAX_SIDE = 8192;
const MAX_PIXELS = 16_000_000;
const BACKGROUND = '#f5f7f5';
const PRESENTATION = ['color', 'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width',
  'stroke-opacity', 'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin',
  'font-family', 'font-size', 'font-weight', 'font-style', 'letter-spacing', 'text-anchor',
  'dominant-baseline', 'paint-order', 'opacity', 'visibility', 'display', 'vector-effect',
  'marker-start', 'marker-mid', 'marker-end'];

/** Добавляет поля в координатах карты и ограничивает память PNG, не обрезая схему. */
export function exportDimensions(bounds) {
  if (![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
      || bounds.width <= 0 || bounds.height <= 0) throw new Error('На карте нет элементов для экспорта.');
  const width = bounds.width + 64, height = bounds.height + 64;
  const scale = Math.min(2, MAX_SIDE / width, MAX_SIDE / height, Math.sqrt(MAX_PIXELS / width / height));
  return { x: bounds.x - 32, y: bounds.y - 32, width, height, scale,
    pixelWidth: Math.max(1, Math.floor(width * scale)), pixelHeight: Math.max(1, Math.floor(height * scale)) };
}

/** Удаляет разделители пути из имени файла; Unicode-название проекта остаётся читаемым. */
export function exportFilename(name, format = 'png') {
  if (!['svg', 'png'].includes(format)) throw new Error('Выберите формат SVG или PNG.');
  const label = String(name || 'code-atlas').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').trim().slice(0, 120);
  return `${label || 'code-atlas'}-map.${format}`;
}

/** Локальные SVG-маркеры не должны ссылаться на URL страницы в скачанном файле. */
function localReferences(value) {
  return value.replace(/url\(["']?[^)"']*#([^)"']+)["']?\)/g, 'url(#$1)');
}

/** Встраивает текущие цвета и шрифты в копию SVG: внешняя CSS-страница изображению недоступна. */
function copyPresentation(source, clone, window) {
  const originals = [source, ...source.querySelectorAll('*')];
  const copies = [clone, ...clone.querySelectorAll('*')];
  for (let index = 0; index < originals.length; index++) {
    const style = window.getComputedStyle(originals[index]);
    for (const property of PRESENTATION) {
      const value = style.getPropertyValue(property);
      if (value) copies[index].style.setProperty(property, localReferences(value));
    }
    // Копия фиксирует текущую сцену и не воспроизводит hover/transition из интерфейса.
    copies[index].style.setProperty('animation', 'none');
    copies[index].style.setProperty('transition', 'none');
  }
}

/** Клонирует рисунок вместе с defs, убирая только преобразование камеры из копии. */
function exportSvg(svg, viewport, dimensions) {
  const document = svg.ownerDocument, window = document.defaultView;
  const clone = svg.cloneNode(true);
  copyPresentation(svg, clone, window);
  const content = clone.querySelector(`#${viewport.id}`);
  if (!content) throw new Error('Не удалось подготовить рисунок карты.');
  content.removeAttribute('transform');
  content.style.removeProperty('transform');
  clone.setAttribute('width', dimensions.pixelWidth);
  clone.setAttribute('height', dimensions.pixelHeight);
  clone.setAttribute('viewBox', `${dimensions.x} ${dimensions.y} ${dimensions.width} ${dimensions.height}`);
  clone.style.setProperty('width', `${dimensions.pixelWidth}px`);
  clone.style.setProperty('height', `${dimensions.pixelHeight}px`);
  clone.style.setProperty('background-color', BACKGROUND);
  const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  for (const property of ['x', 'y', 'width', 'height']) background.setAttribute(property, dimensions[property]);
  background.setAttribute('fill', BACKGROUND);
  clone.insertBefore(background, clone.firstChild);
  return new window.XMLSerializer().serializeToString(clone);
}

/** Сериализует полную сцену одним синхронным проходом и сразу возвращает экранное окно. */
function prepareGraphExport(svg, viewport, vector = false) {
  return withFullSvgScene(svg, () => {
    if (!viewport.children.length) throw new Error('На карте нет элементов для экспорта.');
    const dimensions = exportDimensions(viewport.getBBox());
    if (vector) {
      dimensions.pixelWidth = Math.ceil(dimensions.width);
      dimensions.pixelHeight = Math.ceil(dimensions.height);
      dimensions.scale = 1;
    }
    return { dimensions, text: exportSvg(svg, viewport, dimensions) };
  });
}

/** Начинает локальное скачивание и освобождает Blob после передачи браузеру. */
function downloadBlob(document, blob, filename) {
  const window = document.defaultView;
  const url = window.URL.createObjectURL(blob), link = document.createElement('a');
  try {
    link.href = url; link.download = filename;
    document.body.append(link); link.click();
  } finally {
    link.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(url), 30_000);
  }
}

/** Сохраняет векторную схему без ограничения разрешения; стили и маркеры входят в файл. */
export async function downloadGraphSvg(svg, name) {
  const document = svg.ownerDocument, window = document.defaultView;
  const viewport = svg.querySelector('#viewport');
  if (!viewport) throw new Error('На карте нет элементов для экспорта.');
  await document.fonts?.ready;
  const { dimensions, text } = prepareGraphExport(svg, viewport, true);
  const filename = exportFilename(name, 'svg');
  const result = await requestJson('/api/map-export', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ svg: text, filename }) },
    {fetcher: (...args) => window.fetch(...args)});
  const link = document.createElement('a');
  try {
    // HTTP attachment поддерживается встроенным браузером и не теряет векторную геометрию.
    link.href = result.url; link.download = filename;
    document.body.append(link); link.click();
  } finally { link.remove(); }
  return { ...dimensions, url: result.url, filename };
}

/** Загружает локальный SVG; ошибка декодирования не оставляет кнопку заблокированной. */
function loadImage(window, url) {
  return new Promise((resolve, reject) => {
    const image = new window.Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Браузер не смог подготовить изображение карты.'));
    image.src = url;
  });
}

/** Экспортирует текущую раскрытую сцену, сохраняя камеру и состояние интерфейса. Возвращает размер готового PNG. */
export async function downloadGraphPng(svg, name) {
  const document = svg.ownerDocument, window = document.defaultView;
  const viewport = svg.querySelector('#viewport');
  if (!viewport) throw new Error('На карте нет элементов для экспорта.');
  await document.fonts?.ready;
  const { dimensions, text } = prepareGraphExport(svg, viewport);
  // Политика страницы разрешает data: изображения; Blob-изображения ей недоступны.
  const sourceUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
  const canvas = document.createElement('canvas');
  try {
    const image = await loadImage(window, sourceUrl);
    canvas.width = dimensions.pixelWidth; canvas.height = dimensions.pixelHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Браузер не поддерживает экспорт в PNG.');
    context.fillStyle = BACKGROUND; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('Не удалось создать PNG. Попробуйте экспортировать меньше раскрытых элементов.');
    downloadBlob(document, blob, exportFilename(name, 'png'));
    return dimensions;
  } finally {
    canvas.width = canvas.height = 0;
  }
}

/** Управляет только экспортом: блокирует повторный запуск и сообщает результат рядом с кнопкой. */
export function bindGraphExport({ button, status, svg, name, format = () => 'svg' }) {
  const notice = createExportNotice(status);
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    notice.start();
    button.disabled = true; button.textContent = 'Экспорт…';
    status.hidden = false; status.textContent = 'Готовим изображение всей схемы…';
    try {
      const selected = format();
      if (!['svg', 'png'].includes(selected)) throw new Error('Выберите формат SVG или PNG.');
      const download = selected === 'svg' ? downloadGraphSvg : downloadGraphPng;
      const result = await download(svg, name());
      const reduced = selected === 'png' && result.scale < 1 ? ' · разрешение уменьшено для большой карты' : '';
      status.textContent = selected === 'svg' ? 'SVG готов · качество сохраняется при увеличении'
        : `PNG готов: ${result.pixelWidth} × ${result.pixelHeight}${reduced}`;
      if (selected === 'svg') {
        // Прямая ссылка позволяет повторить скачивание, если браузер остановил автоматическое.
        const link = svg.ownerDocument.createElement('a');
        link.href = result.url; link.download = result.filename; link.textContent = 'Скачать SVG';
        status.append(' · ', link);
      }
      notice.success();
    } catch (error) {
      status.textContent = error.message || 'Не удалось экспортировать карту.';
      notice.error();
    } finally {
      button.disabled = false; button.textContent = 'Экспорт';
    }
  });
}

/**
 * Связывает native кнопку с загрузкой canonical архитектуры; сообщение остаётся во встроенном status.
 * @param {object} options button/status — элементы host UI, format/getModel — синхронные поставщики
 * формата и {graph,options} текущего committed scope; document используется для скачивания Blob.
 * @returns {void} Ошибка сериализации/скачивания показывается в status; кнопка всегда освобождается.
 * JSON сохраняет public evidence; для DSL явно предлагается JSON, если нужны отдельные факты/раскладка.
 */
export function bindArchitectureExport({button, status, format, getModel, document = globalThis.document}) {
  if (!button) return;
  button.addEventListener('click', () => {
    if (button.disabled) return;
    button.disabled = true; status.hidden = false;
    try {
      const {graph, options} = getModel();
      const selectedFormat = format();
      const artifact = architectureExport(graph, {...options, format: selectedFormat});
      downloadBlob(document, new Blob([artifact.text], {type: artifact.mimeType}), artifact.filename);
      const omitted = graph.sceneLimits;
      const partial = omitted?.omittedNodes || omitted?.omittedEdges
        ? ` Частичная область: пропущено ${omitted.omittedNodes} узлов и ${omitted.omittedEdges} связей.` : '';
      status.textContent = (selectedFormat === 'json' ? 'Архитектура сохранена в Atlas JSON.'
        : 'Сохранено. Раскладка будет пересчитана; подробные доказательства и отдельные факты сохраните в Atlas JSON.') + partial;
    } catch (error) { status.textContent = error.message || 'Не удалось экспортировать архитектуру.'; }
    finally { button.disabled = false; }
  });
}
