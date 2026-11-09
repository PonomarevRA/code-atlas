import { matchNodeLabels } from './regex-match.js';
/** Изолирует синтаксические ошибки и дорогой backtracking от главного потока страницы. */
self.onmessage = ({ data }) => {
  try { self.postMessage({ ids: matchNodeLabels(data.nodes, data.pattern) }); }
  catch (error) { self.postMessage({ error: error.message }); }
};
