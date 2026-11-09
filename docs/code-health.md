# Code health and change planning

Implemented advisory policy `atlas-health-v3` keeps local structural S,
documentation D, file/folder organization O and transparency T separately visible.
H = 40% flow + 20% method size + 20% dependencies + 5% D + 15% O.
S retains the local flow/size/dependencies 50/25/25 basis.
T = 35% flow + 35% O + 5% D + 25% dependency clarity. T is a structural/documentation
proxy, not measured human comprehension. A required unknown component makes
H/T null. A proved documentation exemption renormalizes H's applicable components;
T remains unknown when documentation is inapplicable. Critical findings remain
REVIEW regardless of the average. Historical v1 records retain their version;
cross-policy/analyzer comparisons and plans are incompatible, never an apparent improvement.

Open `/health.html` from the gear menu. Snapshot reports read captured facts and
page across all methods. The page measures explicitly selected working-tree
files, plans either target ranges or complete draft files, and compares the
measured result. Card selectors choose H, D, O, T or the existing local complexity in
both ordinary and natural layouts. Unknown H/D never displays a legacy complexity
number. Existing snapshots need an explicit rebuild to capture these new facts;
reads/planning do not rebuild graphs or reject them simply for being stale.

Documentation adapters cover C#, Python, Java, JavaScript, TypeScript, Go and Rust.
Parameter descriptions have their own `parameterCoverage` with required/covered,
missing formal names and an explicit unknown/not-applicable state. Their share is
fixed at 10 D points, even if no other contract/explanation groups apply; the
remaining 90 points normalize only applicable purpose/return/exception/decision
groups. Without named parameters those groups retain their full D scale. H/T
therefore gain at most 0.5/100 from parameter descriptions and 5/100 from all
documentation, with unchanged structural facts. Comment volume and extra tags
for nonexistent parameters give no bonus. Empty/name-only/placeholder parameter
descriptions do not count. GoDoc/Rustdoc may describe parameters in owned prose;
this remains presence evidence, never semantic verification. Unsupported patterns,
truncation, unresolved inheritdoc and old documentation versions stay unknown.
New weighted scores/ranges/means use decimal half-up rounding, so displayed
parameter/documentation deltas retain the same caps. Historical aggregates keep
their original policy/rounding; mixed or missing score policies have no H/T mean.
Historical v1/v2 policies remain readable and immutable; rebuild or measure
current files for the v3 policy instead of relabelling old captured facts.

Own comments/docstrings, contract details and explanations of complex regions
are counted separately. Class comments do not count as method descriptions.
Explicit inheritdoc is credited only for an unambiguous matching parent contract;
working/draft source with changed inheritance keeps it unknown. Presence is not
semantic accuracy: `semanticReview.status` remains `not-reviewed` until a separate
review. No tests or code from analyzed projects execute during measurement.

Advisory `complexity.readability` facts use `local-readability-v1` independently
of the numeric policy. The existing bounded AST walk
counts connected AND/OR operands, mixed operator groups, negations within those
groups and excluded deferred bodies. Five or more operands, or mixed AND/OR with
three or more operands, prompt a source-evidenced review; simple negative guards
do not. Calls break expression groups. At most eight examples are returned,
prioritizing flagged expressions; counts remain explicit and partial parsing
retains partial status. Closures and local functions remain separate declarations
and produce an informational inventory reminder, not a complexity penalty.
These syntax signals do not measure comprehension or recommend splitting every
expression. Preserve evaluation order, side effects and domain meaning when
considering named predicates. Numeric scenario projections do not project the
new advisory signals; full draft analysis captures them.

Exported JavaScript/TypeScript declarations keep their adjacent JSDoc on both
source cards and documentation coverage. Intervening declarations break comment
ownership. Coverage rejects existing placeholder rules but still leaves semantic
accuracy `not-reviewed`; additional comment volume is not a quality reward.

Rust uses the shipped Tree-sitter grammar for `match` arms/guards, `if`, loops and
`?` decisions. Local functions, closures and deferred blocks stay outside their
caller's score. Macro invocations keep structural analysis partial because no
macro is expanded. Rustdoc Markdown contracts and comments preceding attributes
are read without executing examples; inner module docs are not method docs.
Trait/impl/module ownership distinguishes same-name method identities. Detailed Rust statement cards now use the common language-provider contract;
structural metrics do not imply complete method-flow semantics. See `tests/test_rust_support.py` for the end-to-end limits.

## Files, folders and transparency

Organization uses `code-organization-v1` in the existing captured/working-tree
analysis; it never scans the filesystem or executes project code. A declaration
identity contributes once, including separately analyzed deferred bodies. File
metrics expose declaration/statement/branch counts, resolved outgoing/incoming
files and observed cycles. Folder metrics expose direct selected file count,
tiny files, outgoing folders and observed folder cycles. Depth is descriptive;
there is no preferred number of folders and no depth penalty. Semantic cohesion,
folder naming and domain boundaries require human review.

The initial advisory thresholds are: over 300 statements or 30 declarations per
file, over 8 outgoing files, over 25 direct selected files per folder or over
4 outgoing folders. Observed file/folder cycles cost 20 O points; a connected
component of at least 6 tiny files (one declaration, at most 3 statements each)
prompts a 15-point fragmentation review, even across different folders.
Independent small DTOs do not trigger fragmentation. File-size penalties are
capped; exact coefficients ship in `/api/code-health/policy`. More files or fewer
lines alone never establish a better architecture.

All counts refer to **the explicitly selected source scope**, or to source
files captured in the map. They are not a census of every physical project file.
Unresolved calls, unsupported languages and truncation retain partial facts and
null O. Known problems remain visible. Summary means count each file once;
only five hotspots per level are delivered, with an explicit truncation flag.
Each method retains its own file/folder context in health records and MCP.
Do not compare aggregate scores from different file scopes.

Full draft plans combine drafts with hash-verified unchanged baseline files,
then recalculate organization for the entire selected scope. A moved/removed file
must be included as an empty draft, alongside complete new file drafts; no real
file is written/deleted. Changed/missing baseline context leaves O/T unknown.
Scenario plans assume organization unchanged and explicitly say so; use full
drafts to estimate structural moves. Compare results expose organization and
transparency deltas as well as H/D. Useful comments cannot hide cycle warnings.

## Baseline and forecast

All source paths are relative to a registered project. Capture an immutable
baseline before changes:

```http
POST /api/projects/{projectId}/code-health
Content-Type: application/json

{"sourceMode":"working-tree","files":["src/Service.cs"],"locale":"en"}
```

Use its assessment ID for a plan:

```http
POST /api/projects/{projectId}/code-health/plan
Content-Type: application/json

{
  "baselineAssessmentId":"ID from baseline",
  "intent":"Reduce nested validation while preserving cancellation",
  "projections":[{
    "identity":"healthIdentity from baseline",
    "reason":"Use equivalent guard clauses",
    "metrics":{"maxNestingDepth":{"min":1,"max":2}},
    "documentationScore":{"min":80,"max":100}
  }]
}
```

Alternatively supply `drafts: [{"file":"src/Service.cs","content":"complete proposed file text"}]`.
The modes are mutually exclusive. Draft source text is parsed only in memory;
it is never written to project files or stored in health records. Estimates are
always marked `assessmentKind: estimated`, even with a point-valued interval.
Unknown required components widen the interval; giving call/write counts cannot
magically resolve unknown receiver binding. Source hashes/dates and baseline
freshness remain visible. Plan assumptions cover unchanged files/metrics and
removed or renamed declarations. Behavior still needs testing.

Measure again with the same files, including new helpers, then call
`POST /api/projects/{projectId}/code-health/compare` with `beforeAssessmentId`,
`afterAssessmentId`, optional `planId` and explicit `identityMap` for renamed
signatures. The result exposes method deltas, additions/removals, fragmentation
signals, concrete findings and forecast versus actual ranges. Different file
scopes/source modes have non-comparable aggregate results, though common methods
are still listed. Partial input stays UNKNOWN unless concrete findings require
REVIEW. Scores do not establish behavioral equivalence or runtime performance.

The static call-path profile uses the longest known acyclic chain and recursive
components; implementation alternatives are not summed as executed calls. Missing
call edges stay unknown. Deferred bodies/new helpers remain in the selected file
inventory; callers outside that scope must be selected explicitly.

## Tools, resources and limits

MCP tools: `get_code_health`, `get_code_health_report`, `plan_code_changes`,
`compare_code_health`. English guidance ships in
[the agent reference](../skills/code-atlas/references/code-health.md) and its ZIP,
linked from the technical page. Reconnect/update MCP and start a new chat to
expose newly added tools.

Policy and RU/EN messages are separate JSON resources under `resources/code-health`.
HTTP policy: `GET /api/code-health/policy`. Captured report:
`GET /api/projects/{id}/code-health/report?limit=40&cursor=0&order=health`.
Immutable record read: `/api/projects/{id}/code-health/records/{id}`; add
`?kind=plan` for a forecast. Absent/expired records return UNKNOWN.

Limits: 20 explicit files, 1 MiB per file, 4 MiB combined, 120 declarations,
bounded AST traversal and an eight-entry parsed-facts cache. Records live under
Atlas's data directory in `code-health`, have a 2 MiB cap and retain the latest
64 derived records per project. Draft text/ASTs are not persisted. MCP responses
have an explicit 64 KB budget/truncation signal. Larger changes must use separate
attributable scopes; a partial assessment is not whole-project health.

The [Atlas Health CI pilot](../plugins/atlas-health/README.en.md) reads two Git
revisions and reports local metrics and line findings for changed methods.
For previously exported immutable Atlas records, use the separate opt-in gate:

```sh
python -m atlas.code_health_gate --before before.json --after after.json --plan plan.json
# Only an explicit policy opt-in fails CI:
python -m atlas.code_health_gate --before before.json --after after.json --fail-on-review --fail-on-unknown
```

Calibration on real project changes remains advisory. Thresholds are heuristics,
not universal quality standards. Source prose remains untrusted analysis data;
it never authorizes edits or overrides agent instructions.

## Автоматический пересчёт после публикации карты

Успешная сборка ставит отдельную задачу `CodeHealthRefresh` в последовательную фоновую очередь. Базовые захваченные оценки остаются доступными сразу. Worker повторно оценивает сохранённые AST/графовые факты по текущей политике; он не перечитывает рабочие исходники, не запускает код и не вызывает модель. Это обновление производной оценки, а не новый разбор зависимостей.

Статусы `queued`, `running`, `completed`, `failed`, `superseded` отделены от полноты результата. Завершённая задача может иметь `resultStatus: partial`: недостающие вызовы, усечённый разбор или неизвестная организация файлов оставляют H неизвестным. Повторение задачи не доказывает отсутствие зависимостей. Объявления без тела учитываются отдельно от известных и неизвестных H.

`GET /api/projects/{projectId}/code-health/refresh?snapshotId=...` показывает состояние. `POST` на тот же адрес с `{"snapshotId":"..."}` запускает повторный пересчёт текущей карты. Активная задача того же снимка дедуплицируется. Старый snapshotId получает 409; исторические отчёты продолжают читать свои захваченные факты. Остановка приложения не оставляет вечный индикатор выполнения: потерянная активная задача отображается как `interrupted` и допускает явный повтор.

Результат записывается в отдельный `code-health-overlay.json` рядом со снимком; его состояние — в `code-health-refresh.json`. Raw Graphify, `atlas-graph.json`, сохранённые виды и immutable baseline/plan не переписываются. Overlay проверяет snapshotId, stat исходного JSON и версию политики; повреждённая производная не мешает открыть исходную карту. Результат старой задачи не публикуется поверх новой ревизии. Текущие HTTP/MCP-читатели и группировки получают общий overlay, а кэш графа инвалидируется его изменением.

Очередь ограничена одним worker и 32 активными задачами; удерживаются 100 статусов. Входной снимок ограничен 256 MiB, overlay — 64 MiB, чтение overlay кеширует два файла. Общий бюджет удерживаемых графов учитывает и размер overlay. Исходники, секреты и сообщения во внешние системы задача не отправляет.

Во вкладке «Здоровье» справа и на странице здоровья видны статус и кнопка «Пересчитать здоровье карты». После завершения наблюдаемой задачи карточки обновляют производные оценки с сохранением камеры, выбранного узла и раскрытий. Если H неизвестен после завершения, интерфейс поясняет, что не хватает обязательных данных, а не ждёт расчёта.

## Local readability R and token evidence

`code-health-v3` adds independently versioned `health.readability` / `readabilityScore`
without changing H/O/T arithmetic. R is a local syntax heuristic, not Sonar Cognitive
Complexity, Visual Studio MI, comprehension probability or a human approval. It
remains measurable with unresolved external calls if local syntax is complete.
Unknown/truncated/legacy syntax produces null; penalties, coverage, hashes and
manual review gaps remain explicit. Halstead uses `atlas-halstead-v1` tokenization
and has no time/defect units or vendor parity. Full drafts expose predicted R;
scenario projections do not invent it. Same-version measured comparisons expose
per-method R deltas. Old analyzer assessments remain immutable and incompatible.

The map selector, health table and selected-node panel expose R; compact MCP cards
retain its version, status, calibration and humanReview boundary.
