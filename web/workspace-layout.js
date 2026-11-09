import { bindWindowShell, createButton } from './vendor/ag-design/index.js';

const NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  repository: ['M8 3H4v17h13v-4','M8 3h9l4 4v9H8z','M17 3v5h4'],
  search: ['M16 16l5 5','M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
  map: ['M4 4h6v6H4zM14 14h6v6h-6zM17 4v6h-4M7 14v6M7 17h7'],
  details: ['M3 4h18v16H3zM15 4v16M6 8h6M6 12h6'],
  health: ['M3 12h4l3-8 4 16 3-8h4'],
  back: ['M19 12H5M12 5l-7 7 7 7'],
  forward: ['M5 12h14M12 5l7 7-7 7'],
  tools: ['M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'],
};

/** Static shell elements use textContent; existing forms, controls and listeners are moved intact. */
function create(document, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

/** A shared monochrome icon keeps button names in ARIA/title instead of taking toolbar space. */
function decorate(document, button, name, title) {
  const svg = document.createElementNS(NS, 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
  for (const d of ICONS[name]) { const path = document.createElementNS(NS, 'path'); path.setAttribute('d', d); svg.append(path); }
  button.replaceChildren(svg); button.title = title; button.setAttribute('aria-label', title);
  button.setAttribute('data-navigation-ignore', '');
}

/** Native details keeps the compact menu usable by keyboard and closes it when another surface is used. */
function bindMenu(menu, document) {
  const summary = menu.querySelector('summary');
  document.addEventListener('keydown', event => {
    if (!menu.open || event.defaultPrevented || event.key !== 'Escape') return;
    if (menu.contains(event.target) || event.target === document.body) {
      event.preventDefault(); menu.open = false; summary.focus({ preventScroll: true });
    }
  });
  // Tab and native project shortcuts should dismiss the menu just like a pointer click.
  const dismissOutside = event => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  };
  document.addEventListener('pointerdown', dismissOutside);
  document.addEventListener('focusin', dismissOutside);
}

/** Mount a compact activity workspace before existing UI controllers bind; no controls are duplicated. */
export function mountWorkspaceLayout(document) {
  const find = id => document.getElementById(id), workspace = document.querySelector('.workspace');
  const left = find('workspace-left'), right = find('workspace-right'), toolbar = document.querySelector('.workspace-controls');
  const originalLeft = [...left.children], originalRight = [...right.children];
  const rail = create(document, 'nav', 'activity-rail'); rail.setAttribute('aria-label', 'Разделы рабочего пространства');
  rail.setAttribute('aria-orientation', 'vertical');
  const rightRail = create(document, 'nav', 'activity-rail activity-rail-right');
  rightRail.setAttribute('aria-label', 'Выбранная карточка'); rightRail.setAttribute('aria-orientation', 'vertical');
  const definitions = [
    ['repository','left','Проекты','panel-left-toggle'], ['search','left','Поиск по проекту','search-controls-toggle'],
    ['map','left','Настройки карты','activity-map'], ['details','right','Детали узла','panel-right-toggle'],
    ['health','right','Здоровье кода','activity-health'], ['tools','right','Действия карточки','activity-tools'],
  ];
  const blocks = {};
  for (const [name, side, title, buttonId] of definitions) {
    const element = create(document, 'section', 'activity-block'); element.id = `activity-block-${name}`;
    const heading = create(document, 'h2', 'activity-heading', title); heading.id = `${element.id}-title`;
    element.setAttribute('aria-labelledby', heading.id); element.append(heading);
    const button = find(buttonId) || create(document, 'button'); button.id = buttonId; button.type = 'button';
    button.setAttribute('data-activity', name); decorate(document, button, name, title);
    (side === 'left' ? rail : rightRail).append(button); (side === 'left' ? left : right).append(element);
    blocks[name] = { element, button, side };
  }
  const repository = blocks.repository.element, settings = blocks.map.element, tools = blocks.tools.element;
  const statusIndex = originalLeft.indexOf(find('status')), resultsIndex = originalLeft.indexOf(find('results-count'));
  // Keep repository setup/list together; presentation and filtering belong to their own activity.
  for (let index = 2; index < statusIndex; index++) {
    const element = originalLeft[index];
    repository.append(element);
  }
  for (const element of originalLeft.slice(statusIndex + 1, resultsIndex)) settings.append(element);
  for (const id of ['results-count','results','more']) repository.append(find(id));
  const count = find('node-count'); if (count) repository.querySelector('h2').append(count);
  for (const element of originalLeft.slice(0, 2)) element.remove();
  const search = blocks.search.element;
  search.append(find('workspace-search-controls'), find('workspace-discovery'));
  for (const element of originalRight.slice(2)) {
    let target = blocks.details.element;
    if (element.id === 'isolate-node' || element.classList.contains('selected-node-actions') || element.id === 'documentation-view') target = tools;
    if (element.id === 'saved-views-panel') target = settings;
    if (element.classList.contains('advanced-controls')) target = search;
    if (element.classList.contains('git-comparison')) target = repository;
    target.append(element);
  }
  const health = create(document, 'div', 'node-health-panel'); health.id = 'node-health-panel';
  health.append(create(document, 'p', 'muted', 'Выберите карточку на карте или в списке слева, чтобы увидеть её оценки и рекомендации.'));
  const refresh = create(document, 'div', 'health-refresh'); refresh.id = 'health-refresh';
  blocks.health.element.append(refresh, health);
  const context = create(document, 'p', 'small muted', 'Выберите карточку на карте или в списке слева.');
  context.id = 'node-actions-context'; tools.querySelector('h2').after(context);
  for (const element of originalRight.slice(0, 2)) element.remove();
  const footer = originalLeft.find(element => element.tagName === 'FOOTER'); if (footer) repository.append(footer);
  workspace.prepend(rail);
  workspace.append(rightRail);

  const menu = create(document, 'details', 'workspace-menu'); menu.id = 'workspace-menu';
  const summary = create(document, 'summary', '', '•••'); summary.title = 'Меню рабочего пространства'; summary.setAttribute('aria-label', summary.title);
  const content = create(document, 'div', 'workspace-menu-content');
  for (const id of ['map-controls-toggle','map-focus-toggle','open-browser-map']) content.append(find(id));
  const pages = document.querySelector('.workspace-pages');
  content.append(document.querySelector('.environment'));
  menu.append(summary, content);
  bindMenu(menu, document);
  // Product commands stay on the rail when native capability takes over the window commands.
  const railActions = create(document, 'div', 'activity-rail-actions');
  railActions.append(menu, pages);
  rail.append(railActions);
  const brand = create(document, 'a', 'workspace-brand', 'CA'); brand.href = '/'; brand.setAttribute('aria-label', 'Выбрать проект'); brand.title = 'Выбрать проект';
  const historyGroup = create(document, 'nav', 'workspace-history'); historyGroup.setAttribute('aria-label', 'История переходов по карте');
  historyGroup.setAttribute('data-navigation-ignore', '');
  const historyControls = {};
  for (const [name, title] of [['back', 'Назад по карте'], ['forward', 'Вперёд по карте']]) {
    const button = createButton({ document, label: title, variant: 'quiet', disabled: true }); button.id = `map-history-${name}`;
    decorate(document, button, name, title); historyGroup.append(button); historyControls[name] = button;
  }
  const mapFindOpen = createButton({ document, label: 'Найти на карте', variant: 'quiet' }); mapFindOpen.classList.add('map-find-open'); mapFindOpen.id = 'map-find-open';
  decorate(document, mapFindOpen, 'search', 'Найти на карте · Ctrl+F / ⌘F'); mapFindOpen.setAttribute('aria-controls', 'map-findbar');
  toolbar.replaceChildren(brand, historyGroup, find('map-compact-context'), mapFindOpen);
  const statusbar = create(document, 'footer', 'workspace-statusbar'); statusbar.append(find('status'));
  workspace.after(statusbar);
  document.body.classList.add('activity-shell');
  const windowShell = bindWindowShell({document,identity:'Code Atlas',header:toolbar,titleSource:'#map-compact-context',existingBack:'#map-history-back',existingForward:'#map-history-forward',actions:[{selector:'#map-find-open',id:'search'}],panels:[{selector:'#workspace-left',edge:'right',variable:'--expanded-left-width',layout:'.workspace',chromeWidth:96},{selector:'#workspace-right',edge:'left',variable:'--expanded-right-width',layout:'.workspace',chromeWidth:96}]});
  // Общая панель вызывает существующую историю; её исходные кнопки остаются источником состояния, но не дублируются на экране.
  historyGroup.hidden = true;
  document.defaultView.addEventListener('pagehide',()=>windowShell.dispose(),{once:true});
  return { rail, rightRail, blocks, mapFindOpen, historyControls, windowShell, projectPicker: brand };
}

/** Открывает существующий выбор проекта без перезагрузки карты; модифицированные ссылки сохраняют поведение браузера. */
export function bindProjectPicker(link, reveal, select) {
  function open(event) {
    if (event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    reveal();
    select.focus({preventScroll:true});
    select.scrollIntoView?.({block:'nearest'});
  }
  link.addEventListener('click', open);
  return () => link.removeEventListener('click', open);
}
