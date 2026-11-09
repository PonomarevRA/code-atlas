import { wrapDiagramText } from './diagram-text.js';
import { compactValueAssignment } from './method-value-assignment.js';

/** Fully represented two-value alternatives without extracted actions share two output ports. */
export function valueChoiceArms(step) {
  if (step.kind !== 'branch' || step.incomplete || step.arms?.length !== 2) return null;
  const choices = [];
  for (const arm of step.arms) {
    if (arm.exception || arm.steps?.length !== 1 || arm.steps[0].kind !== 'value') return null;
    const value = arm.steps[0];
    choices.push({ label: arm.label, expression: value.expression || value.label, line: value.line,
      reuseResult: Boolean(value.reuseResult) });
  }
  return choices;
}

/** A short complete method with no calls, branches or field summaries fits one source card. */
export function simpleMethodRows(flow, dataEdges = []) {
  if (flow?.status !== 'parsed' || flow.truncated || flow.unsupported?.length || dataEdges.length) return null;
  if (!flow.steps?.length || flow.steps.length > 3) return null;
  const rows = [];
  for (const step of flow.steps) {
    if (!['operation', 'value', 'return'].includes(step.kind)) return null;
    rows.push({ expression: step.sourceStatement || step.expression || step.label, line: step.line, kind: step.kind, stepNumber: String(rows.length + 1) });
    if (step.kind === 'return') break;
  }
  return rows;
}

/** Output rows use the same offsets in geometry, SVG and collision-aware routing. */
export function valueChoicePortOffset(index) { return index === 0 ? -10 : 28; }

/** Only value evaluation can be folded; calls, unknown steps and incomplete arms stay visible. */
function valueOnlyEvaluation(step) {
  if (step.incomplete) return false;
  if (step.kind === 'value') return Boolean(step.expression || step.label);
  if (step.kind !== 'branch' || !['&&', '||'].includes(step.controlKind) || step.arms?.length !== 2) return false;
  return step.arms.every(arm => !arm.exception && arm.emptyReason !== 'not-extracted' && (arm.steps?.length
    ? arm.steps.every(valueOnlyEvaluation) : arm.emptyReason === 'skip'));
}

/** Fold a value-only short circuit into its adjacent result test, retaining original occurrence indices. */
export function compactConditionEntries(steps, complete = true) {
  const entries = [];
  for (let index = 0; index < steps.length; index++) {
    const assignment = complete && compactValueAssignment(steps, index);
    if (assignment) { index = assignment.index; entries.push(assignment); continue; }
    const step = steps[index], next = steps[index + 1];
    if (complete && step.kind === 'branch' && valueOnlyEvaluation(step) && next?.kind === 'branch'
      && next.controlKind === 'if' && !next.incomplete && next.conditionResult === 'reuse'
      && next.conditionExpression && step.line > 0 && next.line === step.line) {
      index += 1;
      entries.push({ index, step: { ...next, conditionResult: undefined,
        label: next.conditionExpression, compactEvaluation: true } });
    } else entries.push({ index, step });
  }
  return entries;
}

/** Complete if arms with one short action or an explicit skip fit two rows; terminal jumps never fold. */
export function compactGuardArms(step) {
  if (step.kind !== 'branch' || step.controlKind !== 'if' || step.incomplete || step.arms?.length !== 2) return null;
  const choices = [];
  for (const arm of step.arms) {
    if (arm.exception || arm.emptyReason === 'not-extracted') return null;
    if (!arm.steps?.length && arm.emptyReason === 'skip') {
      choices.push({ label: arm.label, expression: 'Перейти дальше', line: step.line, skipped: true });
      continue;
    }
    if (arm.steps?.length !== 1) return null;
    const action = arm.steps[0], expression = action.sourceStatement || action.expression || action.label;
    if (!['operation', 'value'].includes(action.kind) || action.incomplete || !expression || expression.length > 120 || /\n/.test(expression)) return null;
    choices.push({ label: arm.label, expression, line: action.line, kind: action.kind });
  }
  return choices.some(choice => !choice.skipped) ? choices : null;
}

/** Geometry and SVG use identical line counts, leaving a separate source-link footer. */
export function compactGuardGeometry(condition, choices, evaluation, assignmentTarget) {
  const inlineRows = Boolean(assignmentTarget);
  const heading = wrapDiagramText(inlineRows ? `${assignmentTarget} =` : condition, 43, 3).lines;
  const headerHeight = (inlineRows ? 12 : 16) + heading.length * 15 + (evaluation ? 18 : 0);
  let offset = headerHeight;
  const rows = choices.map(choice => {
    const lines = wrapDiagramText(choice.expression, inlineRows ? 36 : 43, 2).lines;
    // Object availability is different from result nullness. Shorten only the
    // generated coalescing labels; keep nullable-object conditions explicit.
    const label = choice.label === 'значение не null' ? 'не null'
      : choice.label === 'null → запасное значение' ? 'null' : choice.label;
    const labelLines = inlineRows ? wrapDiagramText(label, 12, 2).lines : [];
    const height = inlineRows ? 12 + Math.max(lines.length * 14, labelLines.length * 12) : 24 + lines.length * 14;
    const row = { ...choice, lines, labelLines, top: offset, height };
    offset += height; return row;
  });
  return { heading, headerHeight, rows, inlineRows, w: 340, h: offset + 28 };
}

/** Compact member assignments keep both value ports on the next transition without a synthetic join card. */
export function directChoiceEdges(nodes, edges) {
  const choices = new Map(nodes.filter(node => node.directChoiceExit).map(node => [node.id, node.valueChoices]));
  const result = [];
  for (const edge of edges) {
    const values = choices.get(edge.source);
    if (!values || edge.sourcePort != null || edge.sequenceKind === 'data') { result.push(edge); continue; }
    for (const [index] of values.entries()) result.push({ ...edge, sourcePort: index, choiceExit: true });
  }
  return result;
}

/** Labelled compact guard ports sit at the corresponding action-row centers. */
export function choicePortOffset(node, index) {
  const row = node.guardGeometry?.rows[index];
  return row ? row.top + row.height / 2 - node.h / 2 : valueChoicePortOffset(index);
}

