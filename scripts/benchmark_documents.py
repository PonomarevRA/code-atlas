"""Bounded synthetic document benchmark; all inputs/state live in /private/tmp."""

from __future__ import annotations

import argparse
import builtins
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import platform
from statistics import median
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import time
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY))

from atlas.documents import store as store_module
from atlas.documents.models import encode
from atlas.documents.store import DocumentStore


class InvocationCounter:
    """Count actual parent calls to run_provider and source capture, not resolver calls."""

    def __init__(self):
        self.parse = store_module.run_provider
        self.capture = store_module.capture
        self.parsers = Counter()
        self.captures = 0
        self.lock = Lock()

    def run_provider(self, provider, *args, **kwargs):
        """Count a real process extraction while preserving production worker behavior."""
        with self.lock:
            self.parsers[provider.id] += 1
        return self.parse(provider, *args, **kwargs)

    def capture_source(self, *args, **kwargs):
        """Count explicit source recaptures separately from indexed reads."""
        with self.lock:
            self.captures += 1
        return self.capture(*args, **kwargs)

    def snapshot(self):
        """Return a detached counter snapshot safe across build threads."""
        with self.lock:
            return {"parserInvocations": sum(self.parsers.values()), "parsersByProvider": dict(self.parsers), "sourceCaptures": self.captures}


class ParentRssSampler:
    """Sample only this benchmark process; worker RSS and short peaks are excluded."""

    def __init__(self):
        import psutil

        self.process = psutil.Process()
        self.stop = Event()
        self.peak = self.process.memory_info().rss
        self.thread = Thread(target=self._sample, daemon=True)

    def _sample(self):
        """Poll every 20 ms without claiming a kernel-recorded high-water mark."""
        while not self.stop.wait(0.02):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __enter__(self):
        self.before = self.process.memory_info().rss
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.after = self.process.memory_info().rss
        self.peak = max(self.peak, self.after)


def normalize_xlsx(path):
    """Remove generation timestamps so equal fixtures have repeatable source hashes."""
    with zipfile.ZipFile(path) as archive:
        parts = {member.filename: archive.read(member) for member in archive.infolist()}
    root = ET.fromstring(parts["docProps/core.xml"])
    for element in root:
        if element.tag.endswith(("}created", "}modified")):
            element.text = "2000-01-01T00:00:00Z"
    parts["docProps/core.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(parts):
            member = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, parts[name])


def create_fixtures(project, sections, yaml_items):
    """Create bounded Markdown/YAML and an XLSX whose dimensions are deliberately sparse."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table

    markdown = ["# Synthetic benchmark\n", "| Name | Rate |\n| --- | --- |\n| Benchmark | 42 |\n"]
    for index in range(sections):
        markdown.append(f"## Section {index}\n\nBenchmark paragraph {index}: " + "captured local evidence " * 4 + "\n")
    (project / "guide.md").write_text("\n".join(markdown), encoding="utf-8")
    yaml = ["---", "title: Benchmark YAML", "defaults: &defaults", "  unit: RUB", "items:"]
    for index in range(yaml_items):
        yaml.extend([f"  - id: {index}", f"    label: Benchmark item {index}", f"    amount: {index}.125"])
    yaml.extend(["copy: *defaults", "...", "---", "second_document: true", "literal: '001.250'"])
    (project / "settings.yaml").write_text("\n".join(yaml) + "\n", encoding="utf-8")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rates"
    for address, value in {"B2": "Name", "C2": "Rate", "B3": "Benchmark", "C3": 42, "E3": "=C3*2", "XFD1048576": "Sparse edge"}.items():
        sheet[address] = value
    sheet.add_table(Table(displayName="RatesTable", ref="B2:C3"))
    workbook.save(project / "sparse.xlsx")
    normalize_xlsx(project / "sparse.xlsx")
    return ["guide.md", "settings.yaml", "sparse.xlsx"]


def storage_stats(root):
    """Report real file sizes, with revision/blob counts to expose deduplication."""
    result = {"totalBytes": 0, "revisionBytes": 0, "blobBytes": 0, "snapshotBytes": 0}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        size = path.stat().st_size
        result["totalBytes"] += size
        if "revisions" in path.parts:
            result["revisionBytes"] += size
        elif "blobs" in path.parts:
            result["blobBytes"] += size
        elif "snapshots" in path.parts:
            result["snapshotBytes"] += size
    result["revisionCount"] = len(list((root / "revisions").glob("*/metadata.json")))
    result["blobCount"] = len([path for path in (root / "blobs").glob("*") if path.is_file()])
    return result


def measure(name, counter, root, action):
    """Measure one phase; parent RSS is sampled and parser/capture deltas are exact."""
    before = counter.snapshot()
    started = time.perf_counter()
    with ParentRssSampler() as rss:
        details = action()
    elapsed = time.perf_counter() - started
    after = counter.snapshot()
    return {
        "phase": name, "elapsedMs": round(elapsed * 1000, 3),
        "parserInvocations": after["parserInvocations"] - before["parserInvocations"],
        "sourceCaptures": after["sourceCaptures"] - before["sourceCaptures"],
        "parentRssBeforeBytes": rss.before, "parentRssAfterBytes": rss.after,
        "parentRssSampledPeakBytes": rss.peak, "storage": storage_stats(root), **details,
    }


def build(store, map_id, files):
    """Run an explicit import to terminal state and return pinned immutable identities."""
    job = store.ensure(map_id, files)
    result = store.wait(job["id"], 30)
    if result["status"] != "completed":
        raise RuntimeError("Benchmark build failed: " + encode(result))
    return {"snapshotId": result["snapshotId"], "cacheHits": result["cacheHits"], "capturedBytes": result["capturedBytes"]}


@contextmanager
def forbid_original_reads(project):
    """Reject Python open/io.open/os.open on originals; retain stat-only freshness freedom."""
    attempted = []
    project = project.resolve()

    def guarded(original):
        """Wrap the open boundary without touching SQLite's read-only index access."""
        def open_file(filename, *args, **kwargs):
            """Fail immediately if indexed reads try to reopen a selected input."""
            if isinstance(filename, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(filename))
                if candidate.resolve().is_relative_to(project):
                    attempted.append(str(candidate.relative_to(project)))
                    raise AssertionError("Indexed read opened original: " + str(candidate))
                if kwargs.get("dir_fd") is not None:
                    attempted.append(str(candidate))
                    raise AssertionError("Indexed read attempted openat on an original")
            return original(filename, *args, **kwargs)
        return open_file

    with patch("builtins.open", guarded(builtins.open)), patch("io.open", guarded(io.open)), patch("os.open", guarded(os.open)):
        yield attempted


def read_targets(store, snapshot):
    """Select real indexed text/table IDs once, before measuring repeated reads."""
    text = next(node for node in store.search("main", "Benchmark paragraph", snapshot_id=snapshot, limit=5)["items"] if node["kind"] == "text")
    table = next(node for node in store.search("main", "RatesTable", snapshot_id=snapshot, limit=5)["items"] if node["kind"] == "table")
    return text, table


def indexed_reads(store, snapshot, text, table):
    """Perform exactly 100 real tree/search/node/content/table queries on SQLite indexes."""
    durations = []
    operations = Counter()
    maximum_bytes = 0
    with forbid_original_reads(store.project_root) as attempted:
        for number in range(100):
            operation = number % 5
            started = time.perf_counter()
            if operation == 0:
                name, result = "tree", store.tree("main", snapshot_id=snapshot, limit=3)
            elif operation == 1:
                name, result = "search", store.search("main", "Benchmark", snapshot_id=snapshot, limit=5)
            elif operation == 2:
                name, result = "node", store.node("main", text["id"], snapshot_id=snapshot)
                assert result["text"].startswith("Benchmark paragraph")
            elif operation == 3:
                name = "content"
                result = store.read_content("main", text["id"], snapshot_id=snapshot, document_id=text["documentId"], limit=64)
                assert result["text"].startswith("Benchmark paragraph")
            else:
                name = "table"
                result = store.table("main", table["id"], snapshot_id=snapshot, row_count=2, column_count=2)
                assert [cell["text"] for cell in result["cells"]] == ["Name", "Rate", "Benchmark", "42"]
            durations.append((time.perf_counter() - started) * 1000)
            operations[name] += 1
            maximum_bytes = max(maximum_bytes, len(encode(result).encode("utf-8")))
    ordered = sorted(durations)
    return {"indexedReads": len(durations), "operations": dict(operations), "readMedianMs": round(median(durations), 3), "readP95Ms": round(ordered[94], 3), "readMaxMs": round(max(ordered), 3), "largestResponseBytes": maximum_bytes, "originalOpenAttempts": len(attempted)}


def source_identity():
    """Record exact implementation hashes because HEAD alone misses the working tree."""
    paths = [Path(__file__).resolve(), *sorted((REPOSITORY / "atlas/documents").glob("*.py")), REPOSITORY / "requirements.lock"]
    return {str(path.relative_to(REPOSITORY)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def run_benchmark(sections=80, yaml_items=160):
    """Use one fresh store, warm cache, shared revisions and deleted historical originals."""
    implementation_before = source_identity()
    with TemporaryDirectory(prefix="atlas-document-benchmark-", dir="/private/tmp") as directory:
        temporary = Path(directory)
        project = temporary / "input"
        project.mkdir()
        files = create_fixtures(project, sections, yaml_items)
        fixture_metadata = [{"path": name, "bytes": (project / name).stat().st_size, "sha256": hashlib.sha256((project / name).read_bytes()).hexdigest()} for name in files]
        counter = InvocationCounter()
        phases = []
        store = DocumentStore(temporary / "state", project, "benchmark")
        try:
            with patch.object(store_module, "run_provider", counter.run_provider), patch.object(store_module, "capture", counter.capture_source):
                phases.append(measure("cold_store_build", counter, store.root, lambda: build(store, "main", files)))
                first = phases[-1]["snapshotId"]
                assert phases[-1]["parserInvocations"] == 3 and phases[-1]["cacheHits"] == 0
                text, table = read_targets(store, first)
                phases.append(measure("warm_build", counter, store.root, lambda: build(store, "main", files)))
                assert phases[-1]["parserInvocations"] == 0 and phases[-1]["cacheHits"] == 3
                assert phases[-1]["snapshotId"] == first
                phases.append(measure("indexed_reads_100", counter, store.root, lambda: indexed_reads(store, first, text, table)))
                assert phases[-1]["parserInvocations"] == phases[-1]["sourceCaptures"] == 0
                (project / "copy.md").write_bytes((project / "guide.md").read_bytes())
                before_dedup = storage_stats(store.root)
                phases.append(measure("cross_map_duplicate_content", counter, store.root, lambda: build(store, "dedup", ["guide.md", "copy.md"])))
                bindings = store.snapshot("dedup")["documents"]
                assert phases[-1]["parserInvocations"] == 0 and phases[-1]["cacheHits"] == 2
                assert bindings[0]["revisionId"] == bindings[1]["revisionId"]
                assert bindings[0]["documentId"] != bindings[1]["documentId"]
                assert phases[-1]["storage"]["revisionCount"] == before_dedup["revisionCount"]
                assert phases[-1]["storage"]["blobCount"] == before_dedup["blobCount"]
                phases[-1].update(sharedRevision=True, distinctDocumentBindings=True)
                original = project / "guide.md"
                original.write_text(original.read_text(encoding="utf-8") + "\nChanged captured generation.\n", encoding="utf-8")
                phases.append(measure("one_file_changed_build", counter, store.root, lambda: build(store, "main", files)))
                assert phases[-1]["parserInvocations"] == 1 and phases[-1]["cacheHits"] == 2
                assert phases[-1]["snapshotId"] != first
                for path in project.iterdir():
                    path.unlink()
                store.close()

                def restart_reads():
                    """Open a new store with originals absent, then read the older snapshot."""
                    nonlocal store
                    with forbid_original_reads(project):
                        store = DocumentStore(temporary / "state", project, "benchmark")
                    result = indexed_reads(store, first, text, table)
                    result.update(originalsRemaining=len(list(project.iterdir())), historicalSnapshot=True)
                    assert result["originalsRemaining"] == 0
                    return result

                phases.append(measure("restart_historical_reads_100_after_original_deletion", counter, store.root, restart_reads))
                assert phases[-1]["parserInvocations"] == phases[-1]["sourceCaptures"] == 0
                revisions = [json.loads(path.read_text(encoding="utf-8")) for path in (store.root / "revisions").glob("*/metadata.json")]
                final = {"phases": phases, "totals": counter.snapshot(), "revisions": [{key: revision[key] for key in ("revisionId", "provider", "nodeCount", "coverage")} for revision in revisions]}
        finally:
            store.close()
    dependencies = {name: version(name) for name in ("markdown-it-py", "PyYAML", "openpyxl", "psutil")}
    if implementation_before != source_identity():
        raise RuntimeError("Implementation changed during benchmark; rerun on a stable source tree.")
    return {
        "schemaVersion": "atlas-document-performance-v1",
        "measuredAt": datetime.now(timezone.utc).isoformat(), "status": "PASS",
        "environment": {"python": platform.python_version(), "os": platform.platform(),
                        "architecture": platform.machine(), "dependencies": dependencies},
        "fixture": {"markdownSections": sections, "yamlItems": yaml_items, "files": fixture_metadata,
                    "xlsxDeclaredDimension": "B2:XFD1048576", "xlsxStoredValueCells": 6,
                    "xlsxFormulaEvaluation": False},
        "method": {
            "runs": 1, "temporaryRoot": "/private/tmp", "temporaryWorkspaceRemoved": True,
            "coldMeaning": "fresh Atlas store and first spawned extraction; OS filesystem caches were not flushed",
            "rss": "20ms sampled parent RSS only; excludes worker processes and may miss short peaks",
            "invocations": "parent wrapper of the actual store.run_provider boundary; production spawned worker unchanged",
            "originalReads": "Python builtins.open/io.open/os.open guard during indexed read phases; originals deleted before restart",
            "sourceFilesStableDuringRun": True,
            "certification": "local bounded synthetic smoke measurement; no SLO or OS sandbox certification",
        },
        "sourceFilesSha256": implementation_before, **final,
    }


def markdown_report(result, output):
    """Write a short human handoff with exact invocation and measurement boundaries."""
    rows = ["| Phase | ms | Parser calls | Captures | Parent peak MiB | Store bytes |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for phase in result["phases"]:
        rows.append(f"| {phase['phase']} | {phase['elapsedMs']:.3f} | {phase['parserInvocations']} | {phase['sourceCaptures']} | {phase['parentRssSampledPeakBytes'] / 1024 / 1024:.2f} | {phase['storage']['totalBytes']} |")
    metrics_table = "\n".join(rows)
    text = f"""# Document performance smoke, 2026-10-21

Результат: **PASS**. Один запуск на синтетических Markdown/YAML и sparse XLSX; пользовательские документы не читались.

Воспроизведение из корня репозитория:

```sh
.venv/bin/python scripts/benchmark_documents.py --output docs/research/document-mods-performance-2026-10-21.json
```

Входы и хранилище создаются в новом каталоге `/private/tmp` и удаляются после измерений.
Cold означает пустое хранилище Atlas; OS caches не сбрасывались.

{metrics_table}

Проверено: warm build без parser calls; одинаковые bytes используют одну revision при разных document bindings;
100 индексных чтений без source capture/parser и ещё 100 чтений исторического snapshot после удаления оригиналов
и перезапуска. Python open/io.open/os.open на исходниках запрещены в фазах чтения.

RSS — только родительский процесс, опрос 20 мс; память workers и короткие пики не измерены.
Время включает наблюдение RSS. Это локальный smoke, не SLO, throughput benchmark больших файлов
или OS sandbox certification. Все времена, размеры, версии, SHA256 входов и исходников находятся в соседнем JSON.
"""
    output.with_suffix(".md").write_text(text, encoding="utf-8")


def main():
    """Run once with fixed bounded fixture defaults and save JSON plus reproduction notes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "docs/research/document-mods-performance-2026-10-21.json")
    args = parser.parse_args()
    result = run_benchmark()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_report(result, output)
    print(json.dumps({"status": result["status"], "output": str(output), "phases": [{"phase": phase["phase"], "ms": phase["elapsedMs"], "parserInvocations": phase["parserInvocations"]} for phase in result["phases"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
