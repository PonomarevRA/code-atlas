---
name: code-atlas-mods
description: "Create, update and validate user mods for Code Atlas API v1: card themes, actions, SVG decorators and custom grouping. Use for mod packages and their activation; use code-atlas-extensions for Atlas core or new source-analysis capabilities."
---

# Code Atlas mod creator

Build a standalone mod package for the user's requested presentation or grouping.
Keep Graphify extraction, Atlas source facts and application settings independent.
Read [mods API v1](references/mods-v1.md) for the manifest, hook and worker contracts.
The skill teaches mod creation; installing it does not execute code. Analysis-only auto selection is a separate host capability described in the SDK reference.

## Choose the smallest extension

- Appearance and double-click actions: declarative `theme` and `behavior` in `mod.json`.
- Additional card visuals: a JS `activate(api)` module with synchronous SVG overlay hooks.
- Grouping: built-in declarative modes first; Python `group(graph)` for a custom partition.

Do not patch Graphify, fixed core registries or GraphView prototypes to implement a
supported mod. If the requested capability is outside API v1, explain the missing
host contract before expanding the task to core development.

## Locate and build the package

Discover the user's Atlas instance and data directory from `/mods.html` or
`GET /api/mods`; do not assume a port or global storage path. Read
`GET /api/mods/schema` when a live instance is available. If Atlas is unavailable,
build a reviewable package from this skill's versioned contract and identify the
runtime checks that remain unverified.

Create `<mods-directory>/<id>/mod.json` for an authorized installation, or a
separate output directory when the user only wants a package. Folder name equals
manifest `id`. Preserve existing packages and local edits; do not overwrite a
conflicting ID or create executable plugins in an analyzed repository.
Use only the assets required by the feature. Dependencies must already be installed;
package generation does not authorize pip installs or a Graphify upgrade.

Card hooks receive detached facts, existing bounds, an overlay and host actions.
Use `textContent` for labels, keep visuals within bounds and preserve existing
source links, selection, control-flow distinctions, ambiguity and health badges.
Attach actions to explicit events, never call navigation during rendering.
Python grouping must return each input ID exactly once, including isolated nodes;
return `[]` for an empty graph. Do not rewrite raw relations or infer runtime behavior.

## Validate, activate and hand off

Validate manifest and paths without executing the package first. Exercise new
scripts and grouping on a small fixture in an isolated temporary data directory.
Check exact node/edge preservation, relevant ID coverage, failure behavior,
disable/re-enable and changed-content invalidation. For visual changes, inspect
actual cards in natural and ordinary layouts, including source/expand actions
when the mod changes them. Distinguish parser/API checks from browser evidence.

For an authorized installation, refresh the catalog and activate the displayed
fingerprint with `POST /api/mods/{id}/activation`. Enable declared dependencies
first. Editing a package changes its fingerprint and grouping cache key; refresh
and explicitly enable the new revision. A theme/grouping change needs no map
extraction rebuild. Do not turn a package-only request into activation.

Executable JS/Python mods run with application user permissions and are not an
OS sandbox. Explain this before enabling third-party code when approval is needed.
Recovery: `/mods.html` loads no user scripts; disable the offending mod or use
`/?mods=off`. Do not delete maps or source files to recover a mod.

Return the package location, capabilities, tested behavior and remaining limits.
Apply the project's code-quality review when code was written. Preserve the
user's existing settings and leave unrelated sample mods disabled.

## Include a wiki with new mods

Recommend a wiki for every new mod. Start with one useful overview covering its purpose, exact inputs/host strategies, activation and update steps, a concrete example, limits and troubleshooting. Distinguish author explanation from runtime evidence and a proposed check from a test actually run.

Declare optional `wiki` metadata in `mod.json`; see [Mod wiki pages](references/mods-v1.md). Put the Markdown inside the package and choose the relevant Atlas guide section. Wiki reading does not activate executable code. Markdown remains part of the whole-package fingerprint: refresh the catalog after edits and explicitly reactivate a changed executable revision when installation is authorized. Package creation does not authorize activation or external wiki publication.

Validate every declared page and same-mod relative link as part of package checks. Preserve unknown/unsupported coverage in examples; documentation must not imply an unavailable analyzer capability.
