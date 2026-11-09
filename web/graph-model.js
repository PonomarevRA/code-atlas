import { aggregateCommunities, codeProjectId } from './filters.js';
import { aggregateProjectFragments } from './project-blocks.js';
import { architectureGraph } from './architecture.js';
import { projectSearchAliases, discoveryTerms, discoveryText } from './discovery-text.js';
/** Индексы одного неизменяемого снимка; маленькие кэши принадлежат модели и очищаются при замене графа. */
export class GraphModel {
  /** Создаёт индекс перед первым запросом интерфейса. */
  constructor(graph = { nodes: [], edges: [] }) { this.setGraph(graph); }
  /** Один раз индексирует текст и инцидентные связи нового снимка. */
  setGraph(graph) {
    this.graph = graph;
    this.nodes = new Map();
    this.texts = new Map();
    this.adjacency = new Map();
    this.filterCache = [];
    this.aggregateCache = new Map();
    this.architectureCache = null;
    this.memberships = new WeakMap();
    this.searchAliases = projectSearchAliases(graph.nodes);
    for (const node of graph.nodes) {
      this.nodes.set(node.id, node);
      const navigation = `${node.label || ''} ${node.file || ''}`.toLocaleLowerCase();
      this.texts.set(node.id, { navigation, discovery:discoveryText(node), filter: `${navigation} ${node.id}`.toLocaleLowerCase() });
      this.adjacency.set(node.id, []);
    }
    for (const edge of graph.edges) {
      this.adjacency.get(edge.source)?.push(edge);
      if (edge.target !== edge.source) this.adjacency.get(edge.target)?.push(edge);
    }
  }
  /** Кэширует принадлежность набору, чтобы выбор символа не сканировал весь результат. */
  nodeIds(nodes) {
    if (!this.memberships.has(nodes)) this.memberships.set(nodes, new Set(nodes.map(node => node.id)));
    return this.memberships.get(nodes);
  }
  /** Обычный поиск по имени/пути не меняет фильтры, граф или набор возвращённых ранее узлов. */
  search(query, nodes = this.graph.nodes) {
    const terms = discoveryTerms(query, this.searchAliases);
    if (!terms.length) return [];
    const result = [];
    for (const node of nodes) if (terms.every(choices => choices.some(term=>this.texts.get(node.id)?.discovery.includes(term)))) result.push(node);
    return result;
  }
  /** Возвращает отфильтрованный набор; два последних варианта покрывают общий фильтр и соседей. */
  filter({ keywords = '', kind = '', relation = '', codeProjectIds = null, neighborOf = null, changedNodeIds = null } = {}) {
    const key = JSON.stringify([keywords, kind, relation, codeProjectIds, neighborOf]);
    const cached = this.filterCache.find(item => item.key === key && item.changedNodeIds === changedNodeIds);
    if (cached) return cached.nodes;
    const terms = keywords.trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean);
    const projects = codeProjectIds === null ? null : new Set(codeProjectIds.map(String));
    const changed = changedNodeIds === null ? null : new Set(changedNodeIds);
    let nodes = [];
    for (const node of this.graph.nodes) {
      if (kind && node.kind !== kind) continue;
      if (projects && !projects.has(codeProjectId(node))) continue;
      if (changed && !changed.has(node.id)) continue;
      if (!terms.every(term => this.texts.get(node.id).filter.includes(term))) continue;
      nodes.push(node);
    }
    if (relation || neighborOf) {
      const ids = new Set(nodes.map(node => node.id));
      const connected = new Set();
      if (neighborOf && ids.has(neighborOf)) connected.add(neighborOf);
      const edges = neighborOf ? this.adjacency.get(neighborOf) || [] : this.graph.edges;
      for (const edge of edges) {
        if (relation && edge.relation !== relation) continue;
        if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
        connected.add(edge.source);
        connected.add(edge.target);
      }
      nodes = nodes.filter(node => connected.has(node.id));
    }
    this.filterCache.unshift({ key, changedNodeIds, nodes });
    this.filterCache.length = Math.min(2, this.filterCache.length);
    return nodes;
  }
  /** Читает только связи выбранного узла, сохраняя повторы и направления исходных фактов. */
  incident(id, nodes, relation = '') {
    const ids = this.nodeIds(nodes);
    const result = [];
    for (const edge of this.adjacency.get(id) || []) {
      if (relation && edge.relation !== relation) continue;
      if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
      result.push(edge);
    }
    return result;
  }
  /** Пересчитывает группировку только при изменении набора/отношения/режима блоков. */
  aggregate(nodes, relation = '', blocks = false) {
    const cached = this.aggregateCache.get(blocks);
    if (cached?.nodes === nodes && cached.relation === relation) return cached.value;
    const value = blocks ? aggregateProjectFragments(this.graph, nodes, { relation }) : { ...aggregateCommunities(this.graph, nodes, { relation }), displayNodes: nodes };
    this.aggregateCache.set(blocks, { nodes, relation, value });
    return value;
  }
  /** Держит один текущий архитектурный вид; выбор внутри страницы повторно использует результат. */
  architecture(nodes, options = {}) {
    const key = JSON.stringify(options);
    const cached = this.architectureCache;
    if (cached?.nodes === nodes && cached.key === key) return cached.value;
    const value = architectureGraph(this.graph, nodes, options);
    this.architectureCache = { nodes, key, value };
    return value;
  }
}
