import { createDesktopAutomation, storedDesktopMapScope } from './desktop-automation.js';
import {mountLoadingStatus} from './loading-status.js';
mountLoadingStatus(document);
/** Общая навигация между независимыми слоями и техническими страницами Atlas. */
import { colorSchemePicker } from './color-scheme.js';
import { menuViewportPlacement } from './menu-placement.js';
let menuRevision = 0;
const referencePages = new Set(['/documents.html', '/language-docs.html', '/library-docs.html',
  '/wiki.html', '/engine-setup.html', '/technical.html']);
const mapRouteKey = 'atlas-reference-map-return';

/** Remembers this tab's last map route. A direct visit falls back to the map home. */
if (location.pathname === '/') {
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(mapRouteKey, location.search + location.hash); }
    catch { /* Storage can be unavailable in private mode. */ }
  });
}

/** Only the local map route can be restored from tab storage. */
function mapReturnHref() {
  try {
    const suffix = sessionStorage.getItem(mapRouteKey);
    if (suffix && /^[?#]/.test(suffix)) return '/' + suffix;
  } catch { /* Direct links work even without storage. */ }
  return '/';
}

/** Moves the existing page menu out of the header and provides a visible map shortcut. */
function mountReferenceRail() {
  if (!referencePages.has(location.pathname)) return;
  const target = document.querySelector('[data-atlas-menu]');
  if (!target) return;
  const rail = document.createElement('aside');
  rail.className = 'atlas-reference-rail';
  const back = document.createElement('a');
  back.className = 'atlas-reference-back';
  back.href = mapReturnHref();
  rail.append(back, target);
  target.dataset.atlasMenu = 'settings';
  document.body.classList.add('has-atlas-reference-rail');
  document.body.prepend(rail);
  return { rail, back };
}

const referenceRail = mountReferenceRail();
if (location.pathname !== '/') {
  window.atlasDesktopAutomation = createDesktopAutomation({
    document,
    location,
    readScope: () => storedDesktopMapScope(() => sessionStorage),
    returnToMap: () => {
      if (referenceRail) referenceRail.back.click();
      else location.assign(mapReturnHref());
      return { navigation: { path: '/' } };
    },
  });
}

/** Updates the rail even when translations for the popup fail to load. */
function labelReferenceRail(locale) {
  if (!referenceRail) return;
  referenceRail.rail.setAttribute('aria-label', locale === 'en' ? 'Atlas navigation' : 'Навигация Atlas');
  referenceRail.back.textContent = locale === 'en' ? 'Map' : 'К карте';
}
labelReferenceRail(document.documentElement.lang === 'en' ? 'en' : 'ru');

/** Перевод общего меню хранится отдельно; более старый запрос не заменяет новый locale. */
async function renderMenu(locale) {
  labelReferenceRail(locale);
  const revision = ++menuRevision;
  const response = await fetch(`/locales/${encodeURIComponent(locale)}/common.json`);
  const translation = response.ok ? await response.json() : {};
  const english = locale === 'en' ? translation : await (await fetch('/locales/en/common.json')).json();
  const labels = {...english, ...translation};
  if (revision !== menuRevision) return;
  for (const target of document.querySelectorAll('[data-atlas-menu]')) {
    const menu = document.createElement('details'); menu.className = 'atlas-menu';
    const summary = document.createElement('summary');
    if (target.dataset.atlasMenu === 'settings') {
      menu.classList.add('atlas-menu-icon');
      summary.setAttribute('aria-label', labels.menu); summary.title = labels.menu;
      const icon = document.createElement('span'); icon.textContent = '⚙\uFE0E'; icon.setAttribute('aria-hidden', 'true');
      summary.append(icon);
    } else summary.textContent = labels.menu;
    const nav = document.createElement('nav'); nav.setAttribute('aria-label', labels.menu);
    for (const [href, label] of [['/', labels.code], ['/data.html', labels.data], ['/documents.html', labels.documents], ['/language-docs.html', labels.references], ['/library-docs.html', labels.libraries], ['/research.html', locale === 'ru' ? 'Разобраться в проекте' : 'Explore project'], ['/mods.html', labels.mods], ['/health.html', labels.health], ['/architecture.html', locale === 'ru' ? 'Архитектурные проверки' : 'Architecture checks'], ['/change-review.html', locale === 'ru' ? 'Обзор изменений' : 'Change review'], ['/wiki.html', 'Wiki Atlas'], ['/technical.html', labels.technical]]) {
      const link = document.createElement('a'); link.href = href === '/' && referenceRail ? mapReturnHref() : href; link.textContent = label;
      if (href === '/research.html') {
        const context = new URLSearchParams(location.search), query = new URLSearchParams();
        for (const key of ['repo', 'node']) if (context.get(key)) query.set(key, context.get(key));
        if (query.size) link.href += '?' + query;
      } else if (href === '/documents.html' || href === '/library-docs.html' || href === '/technical.html' || href === '/health.html' || href === '/architecture.html') {
        const repo = new URLSearchParams(location.search).get('repo');
        if (repo) link.href += '?repo=' + encodeURIComponent(repo);
      }
      if (location.pathname === href) link.setAttribute('aria-current', 'page');
      nav.append(link);
    }
    const appearance = document.createElement('a'); appearance.href = '/appearance.html'; appearance.textContent = locale === 'ru' ? 'Оформление' : 'Appearance';
    const modules = document.createElement('a'); modules.href = '/module-analysis.html';
    const selectedRepo = new URLSearchParams(location.search).get('repo');
    if (selectedRepo) modules.href += '?repo=' + encodeURIComponent(selectedRepo);
    modules.textContent = locale === 'ru' ? 'Модульность классов и папок' : 'Class and folder modules';
    if (location.pathname === '/module-analysis.html') modules.setAttribute('aria-current', 'page');
    nav.append(modules);
    nav.append(appearance);
    menu.append(summary, nav);
    menu.addEventListener('toggle', () => fitMenu(menu));
    const wiki = wikiShortcut(locale); target.replaceChildren(wiki, menu);
    await colorSchemePicker(nav);
  }
}

/** Измеряет исходное размещение заново: накопленное смещение не переносится между окнами. */
function fitMenu(menu) {
  if (!menu.open) return;
  const nav = menu.querySelector('nav');
  nav.style.removeProperty('--atlas-menu-shift');
  nav.style.removeProperty('max-height');
  const placement = menuViewportPlacement(nav.getBoundingClientRect(),
    document.documentElement.clientWidth, document.documentElement.clientHeight);
  nav.style.setProperty('--atlas-menu-shift', `${placement.shiftX}px`);
  nav.style.maxHeight = `${placement.maxHeight}px`;
}

/** Resize сохраняет открытые пункты доступными без изменения состояния меню и фокуса. */
window.addEventListener('resize', () => {
  for (const menu of document.querySelectorAll('.atlas-menu[open]')) fitMenu(menu);
});

/** Постоянная звёздочка открывает руководство без зависимости от выбранной карты или индекса. */
function wikiShortcut(locale) {
  const link = document.createElement('a'); link.href = '/wiki.html'; link.className = 'atlas-wiki-shortcut';
  link.textContent = '★'; link.setAttribute('aria-label', locale === 'ru' ? 'Wiki Atlas — начало работы' : 'Atlas wiki — getting started');
  link.title = locale === 'ru' ? 'Wiki Atlas · примеры и устройство проекта' : 'Atlas wiki · examples and architecture';
  if (location.pathname === '/wiki.html') link.setAttribute('aria-current', 'page');
  return link;
}

/** Клик вне меню закрывает список, не меняя выбранный узел и состояние панелей. */
document.addEventListener('click', event => {
  for (const menu of document.querySelectorAll('.atlas-menu[open]')) {
    if (!menu.contains(event.target)) menu.open = false;
  }
});
/** Escape возвращает фокус к кнопке меню; native summary поддерживает Enter/Space. */
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  const menu = event.target.closest?.('.atlas-menu[open]');
  if (!menu) return;
  menu.open = false; menu.querySelector('summary').focus(); event.preventDefault();
});
document.addEventListener('atlas-locale-change', event => renderMenu(event.detail));
await renderMenu(document.documentElement.lang === 'en' ? 'en' : 'ru');
