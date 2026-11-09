/** Source-derived local steps and call occurrences; raw graph IDs/edges are never rewritten. */
import { compactLinearCells, sequenceLineBlocks } from './method-sequence-blocks.js';
import { valueChoiceArms, simpleMethodRows, compactConditionEntries, compactGuardArms, compactGuardGeometry, directChoiceEdges } from './method-sequence-compact.js';
import { declarationSummary } from './card-documentation.js';
import { infrastructureCard } from './sequence-infrastructure.js';
import { dockExpandedMethods } from './method-expansion-layout.js';
import { sequenceRowHeight } from './sequence-card-layout.js';
const W = 260, H = 154, GAP = 70, ROW = 52, MAX_CARDS = 320, MAX_FRAMES = 12, MAX_DEPTH = 3;

/** Shrink a single short update without removing its source ID or rerouting continue/break semantics. */
function compactLoopUpdate(cell) {
  if (!cell || cell.cards.length !== 1 || cell.frames.length) return cell;
  const node = cell.cards[0], expression = String(node.sourceExpression || node.label || '');
  if (node.kind !== 'operation' || expression.length > 40 || node.compactRow) return cell;
  Object.assign(node, { compactLoopUpdate: true, w: 184, h: 56, x: 92, y: 28 });
  return { ...cell, w: node.w, h: node.h };
}

/** Build a bounded common scene for Natural/Flow and inline method bodies. */
export function buildMethodSequence(graph, rootId, { expandedIds = new Set(), rootData, getData, loadData = () => {}, controlFilters = {}, implementationCatalog = {}, getNode, localReading = false } = {}) {
  const raw = new Map(graph.nodes.map(n => [n.id, n])), cards = [], edges = [], frames = [], positions = new Map();
  const dispatches = new Map(), accesses = new Map(), eventIndexes = new Map();
  for (const edge of graph.edges) {
    const index = edge.relation === 'dispatches_to' ? dispatches : ['reads', 'writes'].includes(edge.relation) ? accesses : null;
    if (!index) continue;
    if (!index.has(edge.source)) index.set(edge.source, []);
    index.get(edge.source).push(edge);
  }
  const data = id => getData?.(id) || raw.get(id) || getNode?.(id);
  let limited = false;
  /** Call-site lookup is linear once per description; chained calls retain distinct end coordinates. */
  function eventAt(description, site) {
    if (!eventIndexes.has(description)) {
      const index = new Map();
      for (const event of description.events || []) {
        const key = `${event.line}:${event.column}`;
        if (!index.has(key)) index.set(key, []);
        index.get(key).push(event);
      }
      eventIndexes.set(description, index);
    }
    const candidates = eventIndexes.get(description).get(`${site?.line}:${site?.column}`) || [];
    const event = candidates.find(e => site.endLine == null || e.endLine === site.endLine && e.endColumn === site.endColumn);
    return { event, suffix: candidates.length > 1 ? `:${event?.endLine}:${event?.endColumn}` : '' };
  }
  /** Presentation cards carry the exact source owner and line, including unresolved calls. */
  function card(id, source, label, kind, extra = {}) {
    if (cards.length >= MAX_CARDS) { limited = true; return null; }
    const compact = { await: [230, 42], join: [36, 36], resume: [240, 36],
      'method-return': [190, 34], 'method-throw': [200, 34] }[kind];
    const metrics = kind === 'entry' ? [300, 96] : kind === 'value' || kind === 'loop' ? [W, 112] : compact;
    const n = { id, label, kind, sourceOwnerId: source.id, file: source.file, codeProjectId: source.codeProjectId,
      w: metrics?.[0] || W, h: metrics?.[1] || H, transition: Boolean(compact), ...extra };
    if (['operation', 'value', 'return', 'throw', 'break', 'continue'].includes(kind)) {
      n.compactSource = true; n.h = sequenceRowHeight(n);
    }
    cards.push(n); return n;
  }
  /** Structural transitions remain distinct from captured calls and data access relations. */
  function link(source, target, caption = 'Далее', kind = 'next', extra = {}) {
    if (source && target) edges.push({ source, target, caption, sequenceKind: kind, ...extra });
  }
  /** Place a cell and its nested geometry as one unit; caller anchors stay on the cell header. */
  function shift(cell, x, y) {
    for (const n of cell.cards) { n.x += x; n.y += y; }
    for (const f of cell.frames) { f.x += x; f.y += y; }
    return cell;
  }
  function single(n) {
    if (!n) return { cards: [], frames: [], w: 0, h: 0, entry: null, exits: [] };
    n.x = n.w / 2; n.y = n.h / 2;
    return { cards: [n], frames: [], w: n.w, h: n.h, entry: n.id, exits: [n.id] };
  }
  /** Normal returns and throws have separate frame ports; jumps use their nearest lexical context. */
  function sequence(steps, owner, prefix, context, depth, ancestors) {
    const cells = [], exits = [], allCards = [], allFrames = [];
    let previous = [], entry = null, x = 0, y = 0, rowHeight = 0, width = 0;
    const complete = owner.description.methodFlow?.status === 'parsed' && !owner.description.methodFlow.truncated && !owner.description.methodFlow.unsupported?.length;
    for (const { index, step } of compactConditionEntries(steps, complete)) {
      if (cards.length >= MAX_CARDS - 4) { limited = true; previous = []; break; }
      const number = prefix ? `${prefix}.${index + 1}` : `${index + 1}`;
      const cell = stepCell(step, owner, number, context, depth, ancestors);
      if (!cell.entry) continue;
      if (!entry) entry = cell.entry;
      for (const previousId of previous) link(previousId, cell.entry);
      previous = cell.exits;
      cells.push(cell);
      if (!cell.exits.length) break; // No invented continuation beyond an unconditional terminal/unknown jump.
    }
    const arranged = compactLinearCells(cells, edges);
    for (const [index, cell] of arranged.entries()) {
      // A revealed method reads down its local lane; its header stays close to the caller.
      if (depth > 0 || localReading) {
        shift(cell, 0, y); width = Math.max(width, cell.w);
        // Adjacent field selections form one reading run; leave enough space
        // for their two result curves, while other source steps keep the normal gap.
        const next = arranged[index + 1];
        const fieldRun = cell.cards[0]?.directChoiceExit && next?.cards[0]?.directChoiceExit;
        y += cell.h + (fieldRun ? 32 : ROW);
      } else {
        if (x && x + cell.w > 1250) { x = 0; y += rowHeight + ROW; rowHeight = 0; }
        shift(cell, x, y); x += cell.w + GAP; rowHeight = Math.max(rowHeight, cell.h); width = Math.max(width, x - GAP);
      }
      allCards.push(...cell.cards); allFrames.push(...cell.frames);
    }
    if ((depth > 0 || localReading) && allCards.length) y -= ROW;
    exits.push(...previous);
    return { cards: allCards, frames: allFrames, w: width, h: y + rowHeight, entry, exits };
  }
  /** Each call site gets a distinct occurrence, even if its target was shown by another caller. */
  function stepCell(step, owner, number, context, depth, ancestors) {
    const site = `${step.line}:${step.column}`, id = `${owner.occurrence}::step:${number}:${site}`;
    const { event, suffix } = step.kind === 'call' ? eventAt(owner.description, step.site) : {};
    const callId = event ? `${owner.occurrence}::inline-call:${event.line}:${event.column}${suffix}` : id;
    const source = owner.source;
    // Path budgets restrict automatic traversal, not navigation to known snapshot symbols.
    const targetIds = new Set([...(event?.targetNodeIds || []), ...(event?.navigationTargetIds || [])]);
    const targets = [...targetIds].map(id => raw.get(id) || getNode?.(id)).filter(Boolean);
    const candidates = [];
    for (const target of targets) {
      const dispatch = (dispatches.get(target.id) || []).map(e => raw.get(e.target)).filter(Boolean);
      candidates.push(...(dispatch.length ? dispatch : [target]));
    }
    const allCandidates = [...new Map(candidates.map(n => [n.id, n])).values()];
    const catalogItems = [], catalogIds = new Set();
    let catalogTotal = 0, catalogLimited = false, catalogTargets = 0;
    for (const target of event?.implementationTargetIds || []) {
      const summary = implementationCatalog[target];
      if (!summary) continue;
      catalogTargets++; catalogTotal = Math.max(catalogTotal, summary.total); catalogLimited ||= summary.truncated;
      for (const item of summary.items) if (!catalogIds.has(item.id)) { catalogIds.add(item.id); catalogItems.push(item); }
    }
    if (catalogTargets && !catalogLimited) catalogTotal = catalogItems.length;
    else catalogTotal = Math.max(catalogTotal, catalogItems.length);
    const implementationItems = catalogItems.length || catalogTotal ? catalogItems : allCandidates.slice(0, 50).map(target => ({ id: target.id,
      label: `${data(target.id)?.callableFacts?.declaringType || String(target.file || '').split('/').pop()?.replace(/\.[^.]+$/, '') || ''}${target.label}`,
      line: data(target.id)?.line || target.line, file: target.file }));
    const unique = allCandidates.slice(0, 6);
    const definition = unique.length === 1 ? unique[0] : targets.length === 1 ? targets[0] : null;
    const expandable = event && unique.some(n => data(n.id)?.methodFlow?.status === 'parsed' || getData && !data(n.id)?.methodFlow) && depth < MAX_DEPTH;
    const opened = expandable && (expandedIds.has(callId) || unique.some(n => expandedIds.has(n.id)));
    const n = card(callId, source, step.kind === 'call' ? (event?.kind && event.kind !== 'call' ? `${event.callee}()` : event?.text || step.label) : step.label || step.kind, step.kind,
      { stepNumber: number, line: step.line, condition: step.label, decision: ['branch', 'loop'].includes(step.kind),
        sourceExpression: step.expression || (event ? `${event.callee || event.text}(${(event.arguments || []).map(a => a.expression || a.text || '').join(', ')})` : step.label),
        valueOutcome: step.kind === 'value', reuseResult: Boolean(step.reuseResult),
        sourceStatement: step.sourceStatement,
        implicitReturn: step.implicitReturn, reuseExpressionResult: step.reuseExpressionResult,
        conditionResult: step.conditionResult, conditionExpression: step.conditionExpression,
        candidateCount: catalogTotal || Math.max(allCandidates.length, event?.navigationTargetCount || 0), shownCandidates: unique.length,
        candidateCountExact: !catalogLimited || catalogTargets <= 1,
        implementationItems, implementationListLimited: catalogLimited || event?.navigationTargetsTruncated || (catalogTotal || allCandidates.length) > implementationItems.length,
        documentation: unique.length === 1 ? data(unique[0].id)?.documentation : undefined,
        complexity: step.kind === 'call' && unique.length === 1 ? data(unique[0].id)?.complexity : undefined,
        health: step.kind === 'call' && unique.length === 1 ? data(unique[0].id)?.health : undefined,
        ownerId: owner.occurrence, canExpand: Boolean(expandable), bodyExpanded: Boolean(opened),
        readTargetId: unique[0]?.id, readTargetIds: unique.map(target => target.id),
        definitionId: definition?.id, definitionLine: definition && (data(definition.id)?.line || definition.line),
        definitionLabel: unique.length > 1 ? 'Объявление ↗' : 'Метод ↗',
        parallel: step.parallel, directAwait: step.directAwait, targetNodeIds: event?.targetNodeIds || [], controlKind: step.controlKind,
        extensions: step.extensions || [] });
    if (!n) return single(null);
    if (n.candidateCount > 1) n.h += 36;
    let cell = single(n);
    if (step.kind === 'return' || step.kind === 'throw') {
      link(n.id, step.kind === 'return' ? context.returnTo : context.throwTo, step.kind === 'return' ? 'Возврат из метода' : 'Исключение', step.kind);
      cell.exits = []; return cell;
    }
    if (step.kind === 'break' || step.kind === 'continue') {
      link(n.id, step.kind === 'break' ? context.breakTo : context.continueTo, step.kind, step.kind);
      cell.exits = []; return cell;
    }
    if (step.kind === 'unknown') { cell.exits = []; return cell; }
    const complete = owner.description.methodFlow?.status === 'parsed' && !owner.description.methodFlow.truncated && !owner.description.methodFlow.unsupported?.length;
    const guards = controlFilters.conditions === false || !complete ? null : compactGuardArms(step);
    const choices = guards || valueChoiceArms(step);
    if (choices) {
      n.valueChoices = choices; n.hideCondition = controlFilters.conditions === false;
      n.w = 300; n.h = 128;
      if (guards || step.assignmentSelection) {
        n.guardGeometry = compactGuardGeometry(step.label, choices, step.compactEvaluation, step.assignmentTarget);
        n.assignmentSelection = step.assignmentSelection;
        n.compactEvaluation = step.compactEvaluation;
        n.w = n.guardGeometry.w; n.h = n.guardGeometry.h;
      }
      cell = single(n);
      if (step.assignmentSelection) {
        n.directChoiceExit = true;
        cell.w += 24; // Reserve short, separate result gutters beside the card.
        return cell;
      }
      const join = card(id + '::join', source, 'Продолжение', 'join', { ownerId: owner.occurrence,
        explanation: guards ? 'Условие проверено: после выбранной ветви перейти к следующему действию.'
          : 'Один из двух результатов получен: перейти к следующему действию.' });
      if (!join) return cell;
      join.x = n.w + GAP + join.w / 2; join.y = n.y;
      cell.cards.push(join); cell.w = n.w + GAP + join.w;
      for (const [index, choice] of choices.entries()) link(n.id, join.id, `${choice.label}: ${choice.expression}`, 'branch', { sourcePort: index });
      cell.exits = [join.id];
      return cell;
    }
    if (opened) {
      const resume = card(callId + '::resume', source, 'Вернуться к следующему шагу', 'resume', { ownerId: owner.occurrence, line: step.line });
      if (!resume) return cell;
      let canResume = false;
      const nestedMethods = [];
      const eligible = unique.filter(target => !ancestors.has(target.id));
      for (const target of eligible) {
        loadData(target.id);
        const description = data(target.id);
        const nested = method(target, description, callId + '::method:' + target.id, depth + 1, new Set([...ancestors, target.id]));
        nestedMethods.push(nested);
        link(n.id, nested.entry, unique.length > 1 || targets.some(t => t.id !== target.id) ? 'Возможная реализация' : 'Вызвать', 'call');
        // Non-awaited async calls do not promise that the whole body completes before caller continuation.
        const asyncCall = description?.callableFacts?.modifiers?.includes('async') && !step.directAwait;
        const unknownBody = description?.methodFlow?.status !== 'parsed' || description?.methodFlow?.truncated
          || description?.methodFlow?.unsupported?.length || limited;
        if (unknownBody) {
          // The caller's following source is known even when the callee's completion is not.
          link(n.id, resume.id, 'Продолжение возможно; тело раскрыто не полностью', 'unknown');
          canResume = true; resume.uncertain = true;
        }
        if (!step.parallel && !asyncCall) {
          for (const exit of nested.exits) { link(exit, resume.id, 'Вернуться', 'return'); canResume = true; }
        } else { link(n.id, resume.id, 'Продолжение; завершение задач не установлено', 'unknown'); canResume = true; resume.uncertain = true; }
      }
      if (eligible.length < unique.length) { link(n.id, resume.id, 'Рекурсия: тело повторно не раскрывается', 'unknown'); canResume = true; resume.uncertain = true; }
      if (allCandidates.length > unique.length) {
        link(n.id, resume.id, 'Исходы остальных реализаций не раскрыты', 'unknown');
        canResume = true; resume.uncertain = true;
      }
      dockExpandedMethods(cell, n, nestedMethods, resume, { gap: GAP, row: ROW });
      if (resume.uncertain && canResume) resume.label = 'Возможное продолжение';
      if (!canResume) { resume.label = 'Продолжение не установлено'; resume.uncertain = true; }
      cell.exits = canResume ? [resume.id] : [];
      return cell;
    }
    if (step.kind === 'branch' || step.kind === 'loop') {
      const join = card(id + '::join', source, step.kind === 'loop' ? 'После цикла' : 'Продолжение', 'join',
        { ownerId: owner.occurrence, controlKind: step.controlKind, explanation: step.kind === 'loop' ? 'Цикл завершён: перейти к следующему шагу после цикла.' : 'Ветви снова сходятся: дальше выполняется следующий шаг этого метода.' });
      if (!join) return cell;
      let laneY = 0, laneW = 0, live = false;
      const loop = step.kind === 'loop';
      const arms = loop ? [{ label: 'тело цикла', steps: step.body || [], emptyReason: step.bodyEmptyReason }] : step.arms || [];
      let conditionCell = null, firstBodyEntry = null;
      if (loop && step.condition?.length) {
        conditionCell = sequence(step.condition, owner, number + '.условие', context, depth, ancestors);
        cell.cards.push(...conditionCell.cards); cell.frames.push(...conditionCell.frames);
        link(conditionCell.exits.at(-1), n.id, 'Проверить условие');
        if (step.controlKind !== 'do') laneY = conditionCell.h + ROW;
      }
      const laneStart = laneY;
      const loopTest = conditionCell?.entry || n.id;
      const continueTargets = new Set([loopTest]);
      for (const [armIndex, arm] of arms.entries()) {
        const armContext = { ...context, ...(loop ? { breakTo: join.id, continueTo: loopTest } : step.controlKind === 'switch' ? { breakTo: join.id } : {}) };
        const update = loop ? compactLoopUpdate(sequence(step.update || [], owner, number + '.обновление', armContext, depth, ancestors)) : null;
        if (update?.entry) continueTargets.add(update.entry);
        const bodyContext = update?.entry ? { ...armContext, continueTo: update.entry } : armContext;
        let body = sequence(arm.steps, owner, `${number}.${armIndex + 1}`, bodyContext, depth, ancestors);
        if (!body.entry && (arm.emptyReason === 'not-extracted' || arm.steps?.length || owner.description.methodFlow?.truncated && arm.emptyReason !== 'skip')) {
          body = single(card(id + '::unrepresented:' + armIndex, source, 'Действия не извлечены', 'unknown',
            { ownerId: owner.occurrence, line: arm.line || step.line, condition: 'Подробности в исходнике; переход не установлен' }));
          body.exits = [];
        }
        if (!body.entry) {
          if (limited) continue;
          const target = loop ? update.entry || loopTest : join.id;
          const caption = loop ? 'Тело без действий; следующая итерация' : `${arm.label} → дальше`;
          const existing = edges.find(e => e.source === n.id && e.target === target && e.sequenceKind === (loop ? 'loop' : 'branch'));
          if (existing) existing.caption += ` / ${arm.label}`;
          else link(n.id, target, caption, loop ? 'loop' : 'branch', { bypass: !loop });
          if (loop && update.entry) {
            shift(update, W + GAP, laneY); laneY += update.h + ROW; laneW = Math.max(laneW, update.w);
            cell.cards.push(...update.cards); cell.frames.push(...update.frames);
            for (const exit of update.exits) link(exit, loopTest, 'Следующая итерация', 'loop');
          }
          if (!loop) live = true;
          continue;
        }
        firstBodyEntry ||= body.entry;
        shift(body, W + GAP, laneY); laneY += body.h + ROW; laneW = Math.max(laneW, body.w);
        cell.cards.push(...body.cards); cell.frames.push(...body.frames);
        link(n.id, body.entry, arm.label, arm.exception ? 'exception' : 'branch');
        if (loop) {
          if (update.entry) {
            const updateX = W + GAP + (update.cards[0]?.compactLoopUpdate ? Math.max(0, laneW - update.w) / 2 : 0);
            shift(update, updateX, laneY); laneY += update.h + ROW; laneW = Math.max(laneW, update.w);
            cell.cards.push(...update.cards); cell.frames.push(...update.frames);
            for (const exit of body.exits) link(exit, update.entry, 'Обновить');
            for (const exit of update.exits) link(exit, loopTest, 'Следующая итерация', 'loop');
          } else for (const exit of body.exits) link(exit, loopTest, 'Следующая итерация', 'loop');
        } else for (const exit of body.exits) { link(exit, join.id, 'Продолжить'); live = true; }
      }
      // A predicate starts beside its first action; centering it against the
      // whole arm makes long bodies appear to execute above their condition.
      n.y = loop ? laneStart + Math.max(n.h / 2, (laneY - laneStart - ROW) / 2) : n.h / 2;
      if (conditionCell && step.controlKind === 'do') {
        shift(conditionCell, 0, laneY);
        n.y = laneY + conditionCell.h + ROW + H / 2;
      }
      // Rejoin below the occupied band on the caller's reading spine. Neither
      // a skipped arm nor the completed body needs to climb back up/right.
      join.x = n.x;
      join.y = Math.max(n.y + n.h / 2, laneY - ROW) + (loop ? ROW : ROW / 2) + join.h / 2;
      cell.cards.push(join); cell.w = Math.max(W + GAP + laneW, conditionCell?.w || 0);
      cell.h = Math.max(n.y + n.h / 2, laneY - ROW);
      if (loop) {
        const testReachable = step.controlKind !== 'do' || edges.some(e => e.target === loopTest);
        if (step.canExit !== false && testReachable) link(n.id, join.id, step.controlKind === 'foreach' ? 'Элементы закончились' : 'Условие ложно', 'branch', { loopExit: true });
        cell.entry = step.controlKind === 'do' ? firstBodyEntry || n.id : loopTest;
        live = step.canExit !== false && testReachable;
      }
      live ||= edges.some(edge => edge.target === join.id);
      cell.exits = live && !step.incomplete ? [join.id] : [];
      if (!live) cell.cards = cell.cards.filter(card => card.id !== join.id);
      else cell.h = Math.max(cell.h, join.y + join.h / 2);
      // Bypass an entire local branch band, including expanded callees, without an empty node.
      const bypassEdges = edges.filter(edge => edge.source === n.id && edge.bypass);
      if (bypassEdges.length) {
        const corridor = join.y - join.h / 2 - 12;
        for (const edge of bypassEdges) {
          edge.bypassOffset = corridor - n.y;
          edge.downwardJoin = true;
        }
      }
      n.controlScope = { dx: -n.x, dy: -n.y, w: cell.w, h: cell.h,
        loop, testId: loopTest, continueTargets: [...continueTargets] };
      join.controlId = n.id;
    }
    return cell;
  }
  /** A frame's normal/exceptional ports remain distinct; incomplete bodies cannot claim a normal return. */
  function method(source, description, occurrence, depth, ancestors) {
    const flow = description?.methodFlow;
    const complete = flow?.status === 'parsed' && !flow.truncated && !flow.unsupported?.length;
    const header = card(occurrence, source, source.label, 'method', { bodyExpanded: true, line: description?.line || source.line,
      complexity: description?.complexity || source.complexity, health: description?.health || source.health,
      callableFacts: description?.callableFacts, documentation: description?.documentation,
      definitionId: source.id, definitionLine: description?.line || source.line, definitionLabel: 'Исходник ↗' });
    const resourceFacts = description?.infrastructure || source.infrastructure || [];
    const simpleRows = resourceFacts.length ? null : simpleMethodRows(flow, accesses.get(source.id) || []);
    if (header && simpleRows && depth <= MAX_DEPTH && frames.length < MAX_FRAMES) {
      const simpleSummary = declarationSummary(header.documentation);
      Object.assign(header, { simpleRows, simpleSummary, w: 300, h: 80 + simpleRows.length * 42 + (simpleSummary ? 20 : 0) });
      header.x = header.w / 2; header.y = header.h / 2;
      const frame = { id: occurrence, label: source.label, sourceOwnerId: source.id, depth, simple: true,
        x: 0, y: 0, w: header.w, h: header.h };
      frames.push(frame);
      return { cards: [header], frames: [frame], w: header.w, h: header.h, entry: header.id, exits: [header.id] };
    }
    const end = card(occurrence + '::return', source, complete ? 'Возврат из метода' : 'Порядок завершения не установлен', complete ? 'method-return' : 'unknown',
      { explanation: `Нормальное завершение ${source.label}: передать управление вызывающему методу.` });
    const failure = card(occurrence + '::throw', source, 'Выход по исключению', 'method-throw');
    if (!header || !end || !failure) return single(header);
    if (frames.length >= MAX_FRAMES || depth > MAX_DEPTH) { limited = true; const cell = single(header); cell.exits = []; return cell; }
    const frame = { id: occurrence, label: source.label, sourceOwnerId: source.id,
      callableKind: description?.callableFacts?.kind || source.callableFacts?.kind,
      x: 0, y: 0, w: 0, h: 0, depth };
    frames.push(frame);
    const owner = { source, description: description || {}, occurrence };
    const context = { returnTo: end.id, throwTo: failure.id };
    const body = sequence(flow?.steps || [], owner, '', context, depth, ancestors);
    header.x = 24 + W / 2; header.y = 28 + H / 2;
    shift(body, 24, H + ROW + 28);
    if (body.entry || complete && !limited) link(header.id, body.entry || end.id, 'Начать');
    for (const exit of body.exits) if (complete && !limited) link(exit, end.id, 'Завершить метод');
    let footerY = H + ROW + 28 + body.h + ROW;
    const ownCards = [header, ...body.cards];
    // Numbers are short local reading references, not a dotted path through branches.
    // Use displayed source order so for-updates follow their body, even though jump ports were prepared first.
    let localStep = 0;
    for (const item of body.cards) if (item.ownerId === occurrence && item.stepNumber) item.stepNumber = String(++localStep);
    if (!complete || limited) {
      const notice = card(occurrence + '::limited', source, description ? 'Порядок частично неизвестен' : 'Читаем тело…', 'unknown',
        { condition: flow?.unsupported?.join('; ') || 'Подробности в исходнике', ownerId: occurrence });
      if (notice) {
        notice.x = 24 + W / 2; notice.y = footerY + H / 2; ownCards.push(notice); footerY += H + ROW;
        if (!body.entry) link(header.id, notice.id, 'Тело не установлено', 'unknown');
      }
    }
    end.x = Math.max(24 + end.w / 2, 24 + body.w - end.w / 2); end.y = footerY + end.h / 2;
    const hasEnd = edges.some(e => e.target === end.id);
    failure.x = end.x; failure.y = footerY + (hasEnd ? end.h + ROW : 0) + failure.h / 2;
    const hasFailure = edges.some(e => e.target === failure.id);
    const exitHeight = (hasEnd ? end.h : 0) + (hasFailure ? (hasEnd ? ROW : 0) + failure.h : 0);
    const dataEdges = accesses.get(source.id) || [];
    const allDataTargets = [...new Set(dataEdges.map(e => e.target))].map(id => raw.get(id)).filter(n => n?.fieldFacts || n?.staticData);
    const dataTargets = allDataTargets.slice(0,3);
    let shownData = 0;
    for (const [index, field] of dataTargets.entries()) {
      const value = field.dataFacts || field.staticData;
      const note = value?.entries?.slice(0,3).map(e => `${e.key} → ${e.value}`).join('; ');
      const entries = value?.entries || [], total = value?.totalEntries ?? entries.length;
      const info = card(occurrence + '::data:' + field.id, field, 'Данные: ' + field.label, 'data',
        { condition: note || value?.initializer || field.fieldFacts?.type || 'значение неизвестно', ownerId: occurrence, line: field.line, staticValue: true,
          sourceExpression: `${field.label}: ${note || value?.initializer || field.fieldFacts?.type || 'значение неизвестно'}`,
          compactSource: true, h: 94,
          dataSummary: { shown: Math.min(3, entries.length), total, truncated: Boolean(value?.truncated) } });
      if (!info) break;
      shownData++;
      // Data summaries form one compact column; bottom-only fields must not widen every method disclosure.
      info.x = 24 + W/2; info.y = footerY + exitHeight + ROW + index * 106 + info.h/2; ownCards.push(info);
      const reads = dataEdges.some(e => e.target === field.id && e.relation === 'reads');
      const writes = dataEdges.some(e => e.target === field.id && e.relation === 'writes');
      link(header.id, info.id, reads && writes ? 'Чтение / запись данных' : writes ? 'Запись данных' : 'Чтение данных', 'data');
    }
    frame.dataSection = allDataTargets.length ? { x: 24, y: footerY + exitHeight + ROW - 12,
      shown: shownData, total: allDataTargets.length } : null;
    if (hasEnd) ownCards.push(end);
    if (hasFailure) ownCards.push(failure);
    frame.w = Math.max(W + 48, body.w + 48);
    frame.h = footerY + exitHeight + 28 + (dataTargets.length ? ROW + shownData * 106 - 12 : 0);
    frame.infrastructureSection = resourceFacts.length ? { x: 24, y: frame.h + 8, shown: 0,
      total: description?.infrastructureTotal ?? source.infrastructureTotal ?? resourceFacts.length } : null;
    if (resourceFacts.length) frame.h += 28;
    for (const fact of resourceFacts.slice(0, 12)) {
      const presentation = infrastructureCard(fact);
      const resource = card(occurrence + '::resource:' + fact.id, source, presentation.label, 'external-resource',
        { line: fact.line, ownerId: occurrence, condition: presentation.context,
          documentation: { summary: presentation.context }, infrastructureFact: fact, infrastructureEvidence: presentation.evidence, h: 130 });
      if (!resource) break;
      resource.x = 24 + W / 2; resource.y = frame.h + resource.h / 2;
      const calls = body.cards.filter(item => item.kind === 'call' && item.line === fact.line && item.ownerId === occurrence);
      link(calls.length === 1 ? calls[0].id : header.id, resource.id, 'Ресурс · статическая связь', 'data');
      ownCards.push(resource); frame.h += resource.h + 20;
      frame.infrastructureSection.shown++;
    }
    return { cards: ownCards, frames: [frame, ...body.frames], w: frame.w, h: frame.h, entry: header.id,
      exits: complete && !limited && edges.some(e => e.target === end.id) ? [end.id] : [] };
  }
  const source = raw.get(rootId) || getNode?.(rootId), root = source && method(source, rootData || data(rootId), rootId, 0, new Set([rootId]));
  if (!root) return null;
  const ingress = graph.nodes.find(n => String(n.id).startsWith('path-entry:') && graph.edges.some(e => e.source === n.id && e.target === rootId));
  const entrance = ingress && card(ingress.id, source, ingress.label, 'entry', { sourceOwnerId: rootId, condition: ingress.httpMethod || (ingress.entryKind === 'test' ? 'Тест-кейс' : 'Вход') });
  shift(root, 40, 40 + (entrance ? entrance.h + ROW : 0));
  if (entrance) {
    entrance.x = 40 + 24 + entrance.w / 2; entrance.y = 40 + entrance.h / 2;
    root.cards.unshift(entrance); root.h += entrance.h + ROW;
    link(entrance.id, rootId, 'Вход', 'call');
  }
  const filtered = filterSequenceControls(root.cards, edges, controlFilters);
  root.cards = filtered.visuals;
  const visible = new Set(root.cards.map(n => n.id));
  for (const n of root.cards) positions.set(n.id, { x: n.x, y: n.y, w: n.w, h: n.h, r: 0, width: n.w, height: n.h,
    blockId: n.blockId, valueChoices: n.valueChoices,
    cardBounds: { left: n.x - n.w / 2, top: n.y - n.h / 2, right: n.x + n.w / 2, bottom: n.y + n.h / 2 } });
  return { sequence: true, rootId, visuals: root.cards, byId: new Map(root.cards.map(n => [n.id, n])),
    visualEdges: directChoiceEdges(root.cards, filtered.edges.filter(e => visible.has(e.source) && visible.has(e.target))), positions,
    layout: { placed: root.cards, frames: root.frames, lineBlocks: sequenceLineBlocks(root.cards),
      controlScopes: root.cards.filter(n => n.controlScope).map(n => ({ ...n.controlScope, id: n.id,
        x: n.x + n.controlScope.dx, y: n.y + n.controlScope.dy })) }, content: root.cards.filter(n => n.id !== rootId),
    bounds: { x: 40, y: 40, width: root.w, height: root.h }, truncated: limited,
    presentationTruncated: root.cards.some(n => n.candidateCount > n.shownCandidates || n.dataSummary?.truncated || n.dataSummary?.total > n.dataSummary?.shown)
      || root.frames.some(f => f.dataSection?.total > f.dataSection?.shown || f.infrastructureSection?.total > f.infrastructureSection?.shown) };
}

/** Hide controls by reconnecting their incoming/outgoing transitions; retain branch labels and back edges. */
function filterSequenceControls(visuals, sourceEdges, filters) {
  const hidden = new Set(visuals.filter(n => n.decision && !n.valueChoices && (n.kind === 'loop' ? filters.loops === false : filters.conditions === false)).map(n => n.id));
  let edges = sourceEdges;
  for (const id of hidden) {
    const incoming = edges.filter(e => e.target === id), outgoing = edges.filter(e => e.source === id);
    const next = edges.filter(e => e.source !== id && e.target !== id);
    for (const a of incoming) for (const b of outgoing) {
      if (a.source === id || b.target === id || next.length >= MAX_CARDS * 4) continue;
      next.push({ ...b, source: a.source, caption: b.sequenceKind === 'branch' ? b.caption : a.caption,
        sequenceKind: a.sequenceKind === 'loop' ? 'loop' : b.sequenceKind });
    }
    edges = next;
  }
  return { visuals: visuals.filter(n => !hidden.has(n.id)), edges };
}
