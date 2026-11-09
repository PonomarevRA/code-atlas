# Atlas Health for CI

[Русский](README.md)

Atlas Health compares two exact Git revisions and reports local metrics for methods affected by the diff. The pilot supports C#, Python, Java, JavaScript, TypeScript, Go, and Rust. Reports include readability R, nesting, size, documentation, analysis coverage, and findings with file and line locations. Graph-dependent H, O, and T are unavailable in the pilot. Reports omit source text and diff excerpts.

A drop of five or more points in R or documentation is a regression. When a change has no line on HEAD, for example a method deletion, it stays in the artifact without an inline finding. A change in declaration identity can appear as a removal and an addition.

The pilot is advisory. Findings do not fail the scan. Execution errors remain visible and are tolerated by the example jobs. Leave the GitHub job out of required branch protection checks.

## GitLab

1. Choose a published full Code Atlas commit SHA containing this directory and the `atlas.ci_health` module. The runner needs read access to the Atlas repository over HTTPS and a Python wheel source. Set the `atlas-repository` input if you use an Atlas mirror.
2. Copy the [component template](gitlab/templates/atlas-health.yml) to `templates/atlas-health.yml` in a component project on the same GitLab instance and commit it. To include by a Catalog version, release the component to the GitLab CI/CD Catalog. The template and Atlas commits can differ.
3. Add the following include to the consumer project's `.gitlab-ci.yml`. Replace the project path and both placeholders. Ensure `test` exists in the consumer pipeline stages. The [include:project example](gitlab/include.example.yml) uses the same template without requiring Catalog registration.

```yaml
include:
  - component: $CI_SERVER_FQDN/platform/code-atlas-components/atlas-health@REPLACE_WITH_COMPONENT_COMMIT_SHA
    inputs:
      stage: test
      atlas-sha: REPLACE_WITH_ATLAS_COMMIT_SHA
```

The consumer configuration must enable `merge_request_event` pipelines. If it uses `workflow: rules`, check that those rules admit the event. Add `tags` to the `atlas-health` job in the consumer configuration if its runners require tags.

Use a Linux container runner with Git and Python 3.12. The job fetches Git objects into a separate bare repository using GitLab's normal read access. It uses `CI_COMMIT_SHA` for detached MR pipelines and `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` for merged-results and merge-train pipelines. The comparison base is `CI_MERGE_REQUEST_DIFF_BASE_SHA`. When the exact objects are unavailable, the job reports an error.

Open the `atlas-health` job artifacts or the MR **Code Quality** report. GitLab looks for a baseline pipeline report when comparing findings. The default-branch job publishes an empty Code Quality baseline because Atlas findings describe an MR diff. For other target branches or older bases without a baseline report, use the job artifacts. Inline diff markers depend on the GitLab version and tier. [CI/CD components](https://docs.gitlab.com/ci/components/) · [Code Quality](https://docs.gitlab.com/ci/testing/code_quality/) · [Baseline pipeline selection](https://docs.gitlab.com/ci/testing/#baseline-pipeline-selection)

## GitHub Actions

1. Copy the [workflow example](github/atlas-health.example.yml) to `.github/workflows/atlas-health.yml` in the consumer repository.
2. Replace `REPLACE_WITH_ATLAS_COMMIT_SHA` with a published full Atlas commit SHA containing the [composite action](github/action.yml). For a private mirror, grant the consumer repository access to the action using GitHub settings.
3. Open a PR and inspect **Checks**, the job summary, and the `atlas-health` artifact.

The workflow uses `pull_request` with `contents: read`. Checkout supplies Git objects. The job does not run code from the PR. The action computes the merge base from the event's exact SHAs and emits up to ten file and line annotations. Paths that differ between PR HEAD and the check revision remain in the full artifact with PR HEAD coordinates. Keep `fetch-depth: 0` so the common ancestor is available. The example targets GitHub.com with `actions/upload-artifact` v4. Check uploader compatibility if you use GitHub Enterprise Server. [Composite actions](https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax) · [Annotations and summaries](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands)

## Reproduce locally

From a trusted Atlas checkout, use Git and Python 3.12. Supply the exact MR or PR commit SHAs. The fifth argument selects a new temporary virtual environment.

```sh
work=$(mktemp -d)
sh plugins/atlas-health/run.sh /absolute/path/to/repo BASE_SHA HEAD_SHA "$work/report" "$work/venv"
```

The output directory contains `atlas-health.json`, `atlas-health.md`, and `gl-code-quality-report.json`. Check the summary for assessed files and methods. Match the two SHAs and analyzer version when comparing runs.
