/**
 * Принимает только нормализованный относительный путь, не разрешая URL, traversal или системный путь.
 * @param {unknown} value Путь из bounded DTO/URL; файлы проверяются повторно реальным data-files API.
 * @returns {string|null} Безопасный адрес относительно выбранного проекта либо null.
 */
export function relativeDataFile(value) {
  if(typeof value!=='string'||!value||value.length>2000||/[\\\x00-\x1f\x7f]/.test(value)||value.startsWith('/')||/^[a-z][a-z0-9+.-]*:/i.test(value))return null;
  if(value.split('/').some(part=>!part||part==='.'||part==='..'))return null;
  return value;
}

/**
 * Формирует только same-origin переход к существующей карте данных; построение по ссылке не запускается.
 * @param {string} projectId ID выбранной регистрации (до200символов).
 * @param {string} file Сохранённый относительный путь, который discovery должен подтвердить.
 * @param {string|null} mapId Необязательный ID уже готовой карты данных из публичного DTO.
 * @returns {string|null} Адрес data.html с encoded query либо null при неверных входах.
 */
export function dataFileURL(projectId,file,mapId=null) {
  const validId=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f\x7f]/.test(value);
  const path=relativeDataFile(file);
  if(!validId(projectId)||!path)return null;
  const query=new URLSearchParams({project:projectId,file:path});
  if(validId(mapId))query.set('map',mapId);
  return '/data.html?'+query;
}

/**
 * Выделяет файл только после совпадения с текущим ответом discovery; остальные файлы не выбираются.
 * @param {Array<object>} files Реальные публичные data-files с относительным path.
 * @param {unknown} requested Путь из ссылки или null для обычного discovery.
 * @returns {string|null} Единственный подтверждённый выбранный путь либо null; не создаёт карту.
 */
export function discoveredDataSelection(files,requested) {
  const path=relativeDataFile(requested);
  return path&&Array.isArray(files)&&files.some(file=>file.path===path)?path:null;
}
