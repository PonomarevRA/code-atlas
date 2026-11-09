# Atlas mods API v1

## Boundaries and installation

Prefer a user mod for card appearance, map interaction and grouping. Change Atlas
core only when a new source-analysis contract or unsupported host capability is
needed. Never patch Graphify or import its internals from a mod. Extraction belongs
to `atlas/integrations/graphify`; Atlas facts are enriched by
`atlas/enrichment_pipeline.py`; optional presentation/grouping belongs to mods.

Open `/mods.html` or `GET /api/mods` to find the **application-owned** mods folder.
Create `<mods-directory>/<id>/mod.json`. Do not put executable plugins in the
analyzed repository; project manifests never activate code. All optional mods are
disabled initially. Read the catalog, then explicitly enable its exact fingerprint
through the UI or `POST /api/mods/{id}/activation` with
`{"enabled":true,"fingerprint":"hash from catalog"}`. New contents require a new
activation. Source/graph reads never activate mods implicitly. Ask the user before
installing/enabling an unrelated third-party executable package.

JSON themes and built-in actions need no script. JS/Python entries execute trusted
local code with application user permissions: they are **not an OS/browser sandbox**.
A broken frontend can be disabled from `/mods.html`, which loads no user scripts,
or bypassed with `/?mods=off`. Python grouping runs in a disposable process with
a 10-second timeout. Project sources/maps are not inputs as executable code.

## Manifest

`GET /api/mods/schema` is the authoritative schema. Unknown fields/API versions
are rejected and listed as errors without preventing other mods from loading.
Folder name must match `id`; identifiers contain lowercase letters/digits/hyphens.

```json
{
  "apiVersion": 1,
  "id": "my-cards",
  "name": "My readable cards",
  "version": "1.0.0",
  "description": "Presentation only; original source facts are unchanged.",
  "theme": {"fill":"#f5f8ff","headerFill":"#e3edff","stroke":"#537ca7","radius":12},
  "behavior": {"doubleClick":"source"},
  "grouping": [{"id":"by-kind","label":"My grouping","mode":"field","field":"kind"}]
}
```

Theme tokens: fill, headerFill, stroke, text (hex colors), radius, borderWidth,
fontSize. They do not rewrite geometry or method facts. Behavior actions:
select, source, expand, enter, isolate; the host owns raw-ID navigation and its
existing action buttons. Single click remains the normal host selection action.

Grouping modes: field (kind/language/codeProjectId/health.level), directory
(prefix depth 1–8), components, python. Declarative modes are deterministic and
cover isolates too. Python entries declare `worker` (relative .py path) and
optional `function` (default `group`). No registry edits or Atlas restart are
required: refresh the catalog and enable the package.

## JS card decorators

Declare `frontend: "mod.js"`. Export `activate(api)`; do not import Atlas web
internals or patch GraphView prototypes. Relative helper imports use the same
active versioned asset directory. Loading errors keep built-in rendering available.

```js
export function activate(api) {
  api.registerCardDecorator(({ node, overlay, bounds, actions }) => {
    const badge = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    badge.setAttribute('x', bounds.left + 12);
    badge.setAttribute('y', bounds.top + 12);
    badge.textContent = node.kind;
    overlay.append(badge);
    // For an explicit button, attach actions.source/expand to its click handler.
  });
}
```

The callback is synchronous. `node` is a small detached immutable projection:
raw id, visualId, label, kind, file, line, comment, compact health and complexity.
`bounds` is the existing card rectangle; keep overlays inside it and avoid
covering host labels/actions/ports. `overlay` is appended only after a successful
callback. Use textContent for project text. Preserve selection, source links,
if/switch/loop semantics, ambiguity/unknown markers and keyboard navigation.
Call actions from explicit events, not during rendering. `api.onDispose(callback)`
registers cleanup. At most eight decorators/cleanup callbacks per mod. Stateful
side effects outside the API require reload to undo; no sandbox is claimed.

## Custom Python grouping

```python
def group(graph):
    """Group current IDs without creating or dropping source declarations."""
    methods, others = [], []
    for node in graph['nodes']:
        (methods if node.get('kind') in {'function', 'method'} else others).append(node['id'])
    return [group for group in [methods, others] if group]
```

Input is JSON `apiVersion:1`, `nodes` with id/label/kind/file/codeProjectId/language/
health.level and weighted undirected `edges` (source,target,weight). Directional
raw edges remain in the application snapshot. Output is a JSON-compatible list of
nonempty lists: every existing ID exactly once, no foreign IDs. The host validates
coverage, sorts partitions, assigns community IDs/coordinates and preserves raw
facts. Return `[]` for an empty graph. No Graphify/Atlas imports are required.
Dependencies must already exist in the Atlas runtime; automatic package installation
is not supported. `requires` names other explicitly enabled mod IDs.

Input/worker output caps: 8 MiB, 50,000 nodes, 100,000 weighted edges, 10 seconds.
The worker uses a verified temporary copy of package assets and never writes
bytecode into the mod folder. It is trusted code with user permissions, not an
OS filesystem sandbox. Invalid partitions/timeouts fail this grouping; built-in
providers and the original published graph remain available.

Limits: 64 catalog records, 64 assets/package, 4 MiB/package, 1 MiB/asset,
128 paths, 32 KiB manifest. Symlinks/traversal are rejected. Content fingerprints
are part of grouping IDs and asset URLs, preventing reuse of previous-version
caches or execution under old activation. Duplicate IDs cannot replace bundled
packages. Enable dependencies first; disabling one makes its dependents inactive.

## Validation and version updates

Use the shipped `mods/readable-cards` as a complete example. Keep it disabled
unless the user wants it enabled. Test in a temporary data directory: unchanged
source nodes/edges/health facts, unknown IDs, omissions/duplicates, empty graphs,
symlinks, changed fingerprints, disable/re-enable, runtime failure, source actions
and camera/selection. Inspect the actual map for both natural and ordinary
layouts. PASS in existing built-in render tests is not proof of a new mod.

Update a mod's files/version, refresh, inspect changed status, explicitly enable
its new fingerprint. Existing maps need no extraction rebuild for themes/grouping.
Atlas feature/source-analysis changes do require an explicit map rebuild. The
embedded Graphify can stay installed while a separately versioned interpreter is
selected on `/mods.html`; update that provider environment independently and
retain its previous environment for rollback. For extraction failures, preserve
the previous successful snapshot instead of patching vendor code silently.

## Local fact workflows

Optional `workflows` declares up to eight unique `{id,label,engine:"langgraph",kind:"research"|"documentation"}` entries. These select bounded host-owned processes, not executable Python from the package. The exact fingerprint and active `requires` are checked on each step. Open `/research.html` after activation; sources stay local, captured and hashed. No model is connected. See `docs/mods-langgraph.md` for limits, explicit recovery and HTTP contracts.

## Application color schemes

`colorSchemes` adds up to eight named application palettes independently of the existing card `theme`:

```json
{"colorSchemes":[{"id":"night","label":"Ocean","base":"dark","tokens":{"accent":"#8bc9fa","cardHeader":"#293f4f"}}]}
```

Namespaced IDs are `mod:<mod-id>:<scheme-id>`; built-in light/dark cannot be replaced. Omitted tokens inherit the base. Only #RGB/#RRGGBB and host-listed token names are accepted; arbitrary CSS and URLs are rejected. GET `/api/color-schemes` lists resolved palettes, and `/api/color-schemes/styles.css` serves CSP-safe declarations without importing executable mods. The appearance page can copy/edit a palette and export a standalone mod.json or create and explicitly enable a new JSON-only mod. Disabled/changed packages fall back to their base. See `docs/color-schemes.md` and bundled `mods/ocean-colors/mod.json`; leave the sample disabled unless requested.


## Project-scoped automatic analysis (HTML host extension)

Declarative analysis-only bundled packages default to auto selection during a map
build. Existing projectRules/buildRules and library languages supply file selectors;
user analysis-only packages opt in with `autoActivate: {"extensions":[".html"],
"manifestNames":[]}`. Extension selectors are exact dot suffixes; markers are exact
basenames. Unknown selectors/capabilities are rejected by the host schema. This
exception replaces the blanket manual-only statement above for analysis-only mods.
Frontend scripts, grouping, themes, behavior, color schemes and workflows cannot
be auto-activated by this declaration. Repository files are never loaded as plugins.

`enabled` in the global catalog still describes manual activation. `analysisMode`
and `autoAnalysisEligible` explain applicability; effective selected providers are
captured in snapshot analysisContext with matched file examples. `auto` is not a
claim that the project uses a particular library at runtime.

POST `/api/mods/{id}/activation` accepts enabled=true/false as before; false is a
persistent manual off. enabled=null restores auto for an eligible displayed version.
POST `/api/mods/analysis-auto` sets the global auto policy using {"enabled":true}.
Disable-all turns off auto too. Existing manual revision pins retain their meaning;
a changed pin stays inactive until explicitly reactivated or returned to auto.
These settings affect the next map build; MCP/UI reads never trigger extraction.

The bundled language-html mod selects the host `html-structure-v1` markupProvider.
Captured htmlFacts are shared by search/get/describe and the map inspector. The
source-role manifest atlas-html.json and limitations are documented in
`docs/html-mod.md`; the mod never executes project JavaScript or a build command.

## Mod wiki pages

A wiki is recommended for every new package and is optional for existing API v1 manifests. Documentation is available without activating the mod. Declare pages explicitly; the catalog returns metadata without page text. Cold package discovery still reads asset bytes, including Markdown, to preserve the whole-package fingerprint:

```json
"wiki": {
  "section": "Explore-Code",
  "pages": [{"id":"overview","title":"How this mod works","path":"wiki/Overview.md"}]
}
```

`section` is one of `Explore-Code`, `Agents-and-MCP`, `Data-and-Updates`, `Development-Guide`, `Mods-and-Wiki`. Use the section that helps the reader find the package; Atlas shows mod pages beneath it. Page IDs match `^[a-z][a-z0-9-]{0,47}$`, are unique within a package, and pages retain declaration order. Declare up to 16 pages; at least one useful page is recommended. Titles contain 1–100 characters. Paths contain at most 200 characters, are package-relative Markdown files (`.md`, case-insensitive), and must pass the normal package path and symlink checks. Each UTF-8 page is at most 64 KiB; existing total asset/count limits still apply.

The overview should state the purpose, supported inputs and exact strategies, activation/update behavior, a representative example, limitations and diagnostics. For analysis mods distinguish declared capability markers from a qualified engine result. For executable mods explain dependencies and user permissions. Wiki text is author documentation, never source/runtime evidence.

`GET /api/mods/wiki` returns the installed wiki catalog without activation. `GET /api/mods/{id}/wiki/{pageId}?fingerprint=...` reads a declared page with `format: "markdown"` and `text`. Pass the catalog fingerprint: a stale package returns 409 and requires a catalog refresh. Missing pages return 404. Reading documentation does not loosen the active executable-asset endpoint.

Use headings, lists, code fences and ordinary text. Same-mod relative Markdown links must target declared pages. External links are HTTP(S) and open only on user action; do not use filesystem or executable URL schemes. HTML is displayed as text rather than executed. Do not depend on arbitrary HTML, images, scripts, repository-relative links or cross-mod navigation.

All documentation remains inside the existing whole-package fingerprint. Editing a wiki changes the revision and can require explicit reactivation of executable code; it never enables code by itself. Keep external Git wiki publication a separate explicit action.
