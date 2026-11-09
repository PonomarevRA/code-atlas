/* Host-owned TS5 Compiler API worker. Project files are virtual data, never loaded as modules. */
'use strict';
const fs = require('fs');
const path = require('path');
const ts = require(process.argv[2]);

/** Validate the API contract separately from installed executable presence. */
function supported() {
  return /^5\./.test(ts.version) && typeof ts.createProgram === 'function' &&
    typeof ts.convertCompilerOptionsFromJson === 'function';
}

if (process.argv[3] === '--probe') {
  process.stdout.write(JSON.stringify({version: ts.version, supported: supported()}));
  process.exit(0);
}
if (!supported()) throw new Error('unsupported-typescript-api');
const incoming = fs.readFileSync(0);
if (incoming.length > 4 * 1024 * 1024) throw new Error('input-limit');
const request = JSON.parse(incoming);
const root = path.resolve(request.root);
const files = new Map();
for (const item of request.files) files.set(path.resolve(root, item.path), item.text);
const reasons = ['query-scope-only', 'ambient-types-and-standard-library-not-loaded',
  'complete-project-reference-closure-unproven'];
for (const reason of (request.scope || {}).reasons || []) reasons.push(reason);
let options = {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  moduleResolution: ts.ModuleResolutionKind.Node10, allowJs: true, checkJs: true,
  jsx: ts.JsxEmit.Preserve};
if (request.configuration) {
  const parsed = ts.readConfigFile(request.configuration, name => {
    if (path.resolve(name) !== path.resolve(request.configuration)) return undefined;
    const value = fs.readFileSync(name);
    if (value.length > 256 * 1024) return undefined;
    return value.toString('utf8');
  });
  if (parsed.error) reasons.push('configuration-parse-error');
  else {
    const converted = ts.convertCompilerOptionsFromJson(parsed.config.compilerOptions || {}, root);
    options = {...options, ...converted.options};
    if (converted.errors.length) reasons.push('unsupported-compiler-options');
    if (parsed.config.extends) reasons.push('configuration-extends-not-loaded');
    if (parsed.config.references) reasons.push('project-references-not-loaded');
  }
} else reasons.push('inferred-project-context');
// The virtual host cannot discover or execute plugins, package sources, scripts or build hooks.
options = {...options, noLib: true, types: [], noEmit: true, allowJs: true, checkJs: true,
  incremental: false, composite: false};
const sourceFiles = new Map();
const directories = new Set([root]);
for (const name of files.keys()) {
  for (let directory = path.dirname(name); directory.startsWith(root); directory = path.dirname(directory)) {
    directories.add(directory);
    if (directory === root) break;
  }
}
const host = {
  getSourceFile(name, version) {
    name = path.resolve(name);
    if (!files.has(name)) return undefined;
    if (!sourceFiles.has(name)) sourceFiles.set(name, ts.createSourceFile(name, files.get(name), version, true));
    return sourceFiles.get(name);
  },
  getDefaultLibFileName: () => '', writeFile() {}, getCurrentDirectory: () => root,
  getCanonicalFileName: name => name, useCaseSensitiveFileNames: () => true,
  getNewLine: () => '\n', fileExists: name => files.has(path.resolve(name)),
  readFile: name => files.get(path.resolve(name)),
  directoryExists: name => directories.has(path.resolve(name)),
  getDirectories: () => [], realpath: name => path.resolve(name),
};
const program = ts.createProgram([...files.keys()], options, host);
const checker = program.getTypeChecker();
// A diagnostic anywhere in the bounded program can affect recovery/overload binding.
const semanticDiagnostics = program.getSemanticDiagnostics();
const syntaxDiagnostics = program.getSyntacticDiagnostics();
const contextDiagnostics = [...program.getOptionsDiagnostics(), ...program.getGlobalDiagnostics(),
  ...syntaxDiagnostics, ...semanticDiagnostics];
if (semanticDiagnostics.length) reasons.push('compiler-semantic-diagnostics-in-context');
if (syntaxDiagnostics.length) reasons.push('compiler-syntax-diagnostics-in-context');
if (contextDiagnostics.length) reasons.push('compiler-diagnostics-in-context');

/** Convert checked UTF16 positions without permitting a column to spill into another line. */
function offset(source, line, column) {
  if (!Number.isInteger(line) || !Number.isInteger(column) || line < 0 || column < 0) return undefined;
  const starts = source.getLineStarts();
  if (line >= starts.length) return undefined;
  const end = line + 1 < starts.length ? starts[line + 1] : source.text.length;
  if (starts[line] + column > end) return undefined;
  return starts[line] + column;
}

/** Locate one exact callsite or deepest reference using bounded descent, not whole-file serialization. */
function selected(source, query) {
  const site = query.callsite;
  const start = offset(source, site.start_line, site.start_column);
  const end = offset(source, site.end_line, site.end_column);
  if (start === undefined || end === undefined) return undefined;
  let current = source;
  for (let depth = 0; depth < 256; depth++) {
    if (query.kind === 'call' && (ts.isCallExpression(current) || ts.isNewExpression(current)) &&
        current.getStart(source) === start && current.getEnd() === end) return current;
    let next;
    ts.forEachChild(current, child => {
      if (!next && child.getFullStart() <= start && child.getEnd() >= end) next = child;
    });
    if (!next) return query.kind === 'reference' ? current : undefined;
    current = next;
  }
  return undefined;
}

/** Publish definition spans only for sources in the captured virtual inventory. */
function target(declaration) {
  if (!declaration) return undefined;
  const source = declaration.getSourceFile();
  if (!files.has(path.resolve(source.fileName))) return undefined;
  // The identifier span sits inside both compiler and Tree-sitter declarations;
  // compiler declaration starts may also include an outer export wrapper.
  let location = declaration.name || declaration;
  let kind = 'symbol';
  if (ts.isFunctionDeclaration(declaration) || ts.isFunctionExpression(declaration) || ts.isArrowFunction(declaration)) {
    kind = 'function';
  } else if (ts.isMethodDeclaration(declaration) || ts.isMethodSignature(declaration)) kind = 'method';
  else if (ts.isConstructorDeclaration(declaration)) kind = 'constructor';
  if (ts.isArrowFunction(declaration) || (ts.isFunctionExpression(declaration) && !declaration.name)) {
    let binding = declaration;
    for (let depth = 0; depth < 24 && binding.parent; depth++) {
      const parent = binding.parent;
      if ((ts.isVariableDeclaration(parent) || ts.isPropertyDeclaration(parent) || ts.isPropertyAssignment(parent)) &&
          parent.initializer === binding && parent.name && ts.isIdentifier(parent.name)) {
        location = parent.name;
        break;
      }
      if (!ts.isParenthesizedExpression(parent) && !ts.isAsExpression(parent) &&
          !ts.isNonNullExpression(parent) && !ts.isSatisfiesExpression(parent)) break;
      binding = parent;
    }
  }
  const start = source.getLineAndCharacterOfPosition(location.getStart(source));
  const end = source.getLineAndCharacterOfPosition(location.getEnd());
  const relative = path.relative(root, source.fileName).split(path.sep).join('/');
  const name = ts.isIdentifier(location) ? location.getText(source) : ts.SyntaxKind[declaration.kind];
  return {symbol_id: `${relative}:${declaration.getStart(source)}:${declaration.getEnd()}`,
    kind, label: name.slice(0, 300),
    source_span: {path: relative, start_line: start.line, start_column: start.character,
      end_line: end.line, end_column: end.character, coordinate_kind: 'utf-16'}};
}

/** Locate declaration candidates; missing project/ambient closure cannot prove a static call. */
function resolve(query) {
  const source = program.getSourceFile(path.resolve(root, query.path));
  if (!source) return {confidence: 'unknown', reason: 'source-not-in-context'};
  const node = selected(source, query);
  if (!node) return {confidence: 'unknown', reason: 'exact-callsite-not-found'};
  let declaration;
  if (query.kind === 'call') {
    const expression = node.expression;
    const type = checker.getTypeAtLocation(expression);
    if (type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.Union)) {
      return {confidence: 'unknown', reason: 'dynamic-or-ambiguous-callee'};
    }
    if (ts.isPropertyAccessExpression(expression) || ts.isElementAccessExpression(expression)) {
      const receiver = checker.getTypeAtLocation(expression.expression);
      if (receiver.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown | ts.TypeFlags.Union)) {
        return {confidence: 'unknown', reason: 'dynamic-or-ambiguous-receiver'};
      }
    }
    const signature = checker.getResolvedSignature(node);
    declaration = signature && signature.declaration;
  } else {
    let symbol = checker.getSymbolAtLocation(node);
    if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
    const declarations = symbol && symbol.declarations;
    if (declarations && declarations.length === 1) declaration = declarations[0];
  }
  const resolved = target(declaration);
  if (!resolved) return {confidence: 'unknown', reason: 'declaration-unresolved-or-outside-context'};
  if (query.kind === 'call' && !['function', 'method', 'constructor'].includes(resolved.kind)) {
    return {confidence: 'unknown', reason: 'signature-without-callable-definition'};
  }
  const start = node.getStart(source), end = node.getEnd();
  const invalid = contextDiagnostics.some(item => item.file === source && item.start !== undefined &&
    item.start < end && item.start + (item.length || 1) > start);
  // Even a clean staged program omits ambient overloads, dependencies and reference context.
  // A request flag is not proof of completeness; this worker currently emits candidates only.
  return {confidence: 'candidate', target: resolved,
    reason: invalid ? 'compiler-diagnostic-at-query' : 'complete-project-reference-closure-unproven'};
}
const results = request.queries.map(query => resolve(query));
process.stdout.write(JSON.stringify({results, reasons}));
