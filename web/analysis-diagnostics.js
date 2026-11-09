import {el} from './dom.js';
import {dataFileURL} from './data-file-navigation.js';

/**
 * Объясняет только известное состояние извлечения, не приравнивая отсутствие диагностики к успеху.
 * @param {object|null} diagnostics Публичный version1 analysisDiagnostics того же снимка.
 * @returns {object} known/partial/counts/text; неизвестные числа остаются null, а не нулём.
 */
export function analysisDiagnosticsState(diagnostics) {
  const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
  if(diagnostics?.version!==1 || !['complete','partial'].includes(diagnostics.analysisStatus))return {known:false,partial:false,text:'Полнота анализа этого снимка не указана.'};
  const errorCount=count(diagnostics.errorCount),failedFileCount=count(diagnostics.failedFileCount);
  const processedFileCount=count(diagnostics.processedFileCount),sourceFileCount=count(diagnostics.sourceFileCount),excludedDataCount=count(diagnostics.excludedDataCount);
  const partial=diagnostics.analysisStatus==='partial';
  let text=partial?'Карта частична. Доступные узлы можно исследовать.':'Ошибки извлечения в этом снимке не зарегистрированы.';
  if(partial&&failedFileCount>0)text+=' Файлов с ошибками: '+failedFileCount+'.';
  if(partial&&excludedDataCount>0)text+=' Файлов для отдельного слоя данных: '+excludedDataCount+'.';
  if(processedFileCount!==null&&sourceFileCount!==null)text+=' Обработано файлов: '+processedFileCount+' из '+sourceFileCount+'.';
  return {known:true,partial,errorCount,failedFileCount,processedFileCount,sourceFileCount,excludedDataCount,text};
}

/**
 * Создаёт literal карточку ошибки файла и только поддержанные same-origin действия.
 * @param {object} issue Публичные code/path/message/nodeId/alternative; не HTML, команда или произвольный URL.
 * @param {object} options projectId фиксирует регистрацию; onNode открывает diagnostic rawID в том же снимке.
 * @returns {HTMLElement} Статья с причиной и альтернативой данных; не запускает parser/import/rebuild.
 */
export function analysisIssueCard(issue,{projectId,onNode}={}) {
  const card=el('article',undefined,'analysis-issue');card.dataset.analysisIssue=String(issue.code||'unknown');
  card.append(el('h4',issue.path||'Элемент без указанного пути'),el('p',issue.message||'Причина ошибки не указана.'),el('code',issue.code||'UNKNOWN'));
  if(issue.nodeId&&onNode){const node=el('button','Показать ошибку на карте');node.type='button';node.addEventListener('click',()=>onNode(issue.nodeId));card.append(node);}
  const alternative=issue.alternative;
  if(alternative?.kind==='data-map'){
    const url=dataFileURL(projectId,alternative.file);
    if(url){const link=el('a','Открыть этот файл как данные');link.href=url;card.append(link,el('p','Используется существующий слой JSON/CSV. Карта данных ещё не построена: проверьте выбранный файл и подтвердите построение на следующей странице.','small muted'));}
  }

  return card;
}
