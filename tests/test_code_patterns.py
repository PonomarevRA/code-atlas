"""Exact pattern identities, bounded lookup and single-source wiki contracts."""

from copy import deepcopy
from contextlib import closing
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from atlas.code_patterns import PatternCatalog, PatternCatalogError
from atlas.code_patterns.catalog import content_hash
from atlas.code_patterns.wiki import build, render_pages

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources" / "code-patterns"


@pytest.fixture
def catalogue():
    """
    Load the installed source catalogue without network access or execution.
    """
    return PatternCatalog(RESOURCES)


def one_record():
    """
    Return an independent source record for invalid-edition fixtures.
    """
    return json.loads((RESOURCES / "functional-core.json").read_text())


def write_record(directory, record):
    """
    Write one synthetic installed edition using its declared resource filename.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record["id"] + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False))
    return path


def standalone_record():
    """
    Remove cross-card links so a one-card fixture is a complete catalogue.
    """
    record = one_record()
    record["related"] = []
    return record


def test_initial_catalogue_and_public_result_are_independent(catalogue):
    """
    Discovery covers the twelve actual cards and cannot mutate retained content.
    """
    page = catalogue.search(limit=20)
    assert page["total"] == 12
    assert page["nextOffset"] is None
    assert len({item["id"] for item in page["items"]}) == 12
    result = catalogue.get("functional-core")
    assert result["consumerQualification"]["status"] == "not-assessed"
    assert result["pattern"]["status"] == "draft"
    result["pattern"]["whenToUse"].clear()
    assert catalogue.get("functional-core")["pattern"]["whenToUse"]


def test_pages_keep_one_catalogue_identity(catalogue):
    """
    Stable continuation neither drops nor duplicates cards.
    """
    identifiers, offset = [], 0
    while True:
        page = catalogue.search(offset=offset, limit=3, catalog_version=catalogue.catalog_version)
        identifiers.extend(item["id"] for item in page["items"])
        if page["nextOffset"] is None:
            break
        offset = page["nextOffset"]
    assert identifiers == sorted(identifiers)
    assert len(identifiers) == len(set(identifiers)) == 12
    assert catalogue.search(offset=100)["items"] == []


@pytest.mark.parametrize("query,expected", [
    ("чистая функция", "functional-core"),
    ("папка I/O", "ports-adapters"),
    ("MCP обновление", "stable-mcp-facade"),
])
def test_selection_scenarios(catalogue, query, expected):
    """
    Lexical suggestions expose the intended scenario, never a quality verdict.
    """
    page = catalogue.search(query, limit=20)
    assert expected in {item["id"] for item in page["items"]}
    card = catalogue.get(expected)["pattern"]
    assert card["whenNotToUse"] and card["alternatives"]
    assert "Candidates only" in page["interpretation"]
    assert catalogue.search("definitely-no-pattern-for-this-needle")["items"] == []


def test_exact_identity_and_changed_content(tmp_path, catalogue):
    """
    Same semantic version with changed text cannot satisfy an old exact reference.
    """
    record = standalone_record()
    write_record(tmp_path, record)
    old = PatternCatalog(tmp_path)
    old_card = old.get(record["id"])["pattern"]
    record["problem"] += " Changed source."
    write_record(tmp_path, record)
    changed = PatternCatalog(tmp_path)
    assert old.catalog_version != changed.catalog_version
    assert old.get(record["id"])["pattern"] == old_card
    with pytest.raises(PatternCatalogError) as error:
        changed.get(record["id"], content_hash=old_card["contentHash"])
    assert (error.value.code, error.value.status) == ("content_changed", 409)
    with pytest.raises(PatternCatalogError) as error:
        changed.search(catalog_version=old.catalog_version)
    assert error.value.code == "catalog_changed"


def test_missing_and_unavailable_version_do_not_fall_back(catalogue):
    """
    Absent identities are explicit errors instead of implicit latest-card reads.
    """
    for kwargs, code in [({"pattern_id": "missing"}, "pattern_not_found"),
                         ({"pattern_id": "functional-core", "version": "99.0.0"}, "version_not_found")]:
        with pytest.raises(PatternCatalogError) as error:
            catalogue.get(**kwargs)
        assert (error.value.code, error.value.status) == (code, 404)


@pytest.mark.parametrize("kwargs", [
    {"offset": True}, {"offset": -1}, {"offset": 257}, {"limit": False},
    {"limit": 0}, {"limit": 21}, {"query": "a" * 501}, {"query": None},
    {"status": []}, {"status": "automatically-approved"}, {"catalog_version": 1},
])
def test_invalid_lookup_is_bounded(catalogue, kwargs):
    """
    Caller bounds reject rather than coerce incompatible input types.
    """
    with pytest.raises(PatternCatalogError) as error:
        catalogue.search(**kwargs)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("mutate", [
    lambda record: record.pop("whenNotToUse"),
    lambda record: record.update(status={}),
    lambda record: record.update(schemaVersion="unknown"),
    lambda record: record.update(related=["nonexistent"]),
    lambda record: record.update(unexpected="extra field"),
    lambda record: record["sources"][0].update(url="javascript:alert(1)"),
    lambda record: record["sources"][0].update(url="https://user:password@example.org/"),
    lambda record: record["sources"][0].update(url="https://[broken"),
    lambda record: record["example"].update(code="def invalid(:"),
])
def test_invalid_source_records_fail_closed(tmp_path, mutate):
    """
    Bad records do not become partially trusted catalogue entries.
    """
    record = standalone_record()
    mutate(record)
    write_record(tmp_path, record)
    with pytest.raises(PatternCatalogError) as error:
        PatternCatalog(tmp_path)
    assert error.value.code == "invalid_catalog"
    assert error.value.status == 500


def test_deprecated_card_is_visible_but_never_consumer_qualified(tmp_path):
    """
    Lifecycle filters preserve exact deprecated records without changing evidence.
    """
    record = standalone_record()
    record["status"] = "deprecated"
    write_record(tmp_path, record)
    catalogue = PatternCatalog(tmp_path)
    assert catalogue.search(status="draft")["total"] == 0
    assert catalogue.search(status="deprecated")["total"] == 1
    assert catalogue.get(record["id"])["consumerQualification"]["status"] == "not-assessed"


def test_resource_limits_and_symlinks(tmp_path):
    """
    Oversized or redirected installed files cannot bypass resource guards.
    """
    path = tmp_path / "functional-core.json"
    path.write_bytes(b" " * 16001)
    with pytest.raises(PatternCatalogError):
        PatternCatalog(tmp_path)
    path.unlink()
    path.symlink_to(RESOURCES / path.name)
    with pytest.raises(PatternCatalogError):
        PatternCatalog(tmp_path)


def test_record_hash_ignores_json_formatting_but_not_meaning(tmp_path):
    """
    Source indentation is not a new edition; changed explanatory content is.
    """
    record = standalone_record()
    path = write_record(tmp_path, record)
    initial = PatternCatalog(tmp_path)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=4, sort_keys=True))
    reordered = PatternCatalog(tmp_path)
    assert initial.catalog_version == reordered.catalog_version
    assert reordered.get(record["id"])["pattern"]["contentHash"] == content_hash(record)


def test_wiki_projection_matches_full_agent_cards(catalogue):
    """
    Every card identity, example, constraint and source is shared by both readers.
    """
    pages = render_pages(catalogue)
    assert len(pages) == 13
    for summary in catalogue.search(limit=20)["items"]:
        card = catalogue.get(summary["id"])["pattern"]
        page = pages[card["wikiPage"] + ".md"]
        assert card["contentHash"] in page
        assert catalogue.catalog_version in page
        assert card["example"]["code"].strip() in page
        assert card["counterexample"]["code"].strip() in page
        assert all(value in page for value in card["whenNotToUse"])
        assert all(source["url"] in page for source in card["sources"])
        assert (ROOT / "docs/wiki" / (card["wikiPage"] + ".md")).read_text() == page


def test_build_check_detects_drift_without_overwriting(tmp_path):
    """
    Check mode rejects stale wiki rather than silently repairing a release input.
    """
    record = standalone_record()
    write_record(tmp_path / "resources/code-patterns", record)
    wiki = tmp_path / "docs/wiki"
    wiki.mkdir(parents=True)
    assert build(tmp_path, check=False) == 2
    page = wiki / "Pattern-functional-core.md"
    page.write_text("# Stale\n")
    with pytest.raises(RuntimeError, match="stale"):
        build(tmp_path, check=True)
    assert page.read_text() == "# Stale\n"


@pytest.mark.parametrize("redirected", ["docs", "wiki"])
def test_generator_rejects_symlink_directory_before_writes(tmp_path, redirected):
    """
    A redirected source directory cannot create pattern pages outside the project.
    """
    write_record(tmp_path / "resources/code-patterns", standalone_record())
    victim = tmp_path / "outside"
    victim.mkdir()
    sentinel = victim / "keep.txt"
    sentinel.write_text("unchanged")
    if redirected == "docs":
        (victim / "wiki").mkdir()
        (tmp_path / "docs").symlink_to(victim, target_is_directory=True)
    else:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs/wiki").symlink_to(victim, target_is_directory=True)
    before = sorted(str(path.relative_to(victim)) for path in victim.rglob("*"))
    with pytest.raises(RuntimeError, match="ordinary project directory"):
        build(tmp_path, check=False)
    assert sentinel.read_text() == "unchanged"
    assert sorted(str(path.relative_to(victim)) for path in victim.rglob("*")) == before


def test_generator_checks_later_symlink_target_before_any_write(tmp_path):
    """
    An invalid final target must not permit earlier generated pages to be changed.
    """
    write_record(tmp_path / "resources/code-patterns", standalone_record())
    wiki = tmp_path / "docs/wiki"
    wiki.mkdir(parents=True)
    earlier = wiki / "Pattern-functional-core.md"
    earlier.write_text("preserve earlier page")
    victim = tmp_path / "victim.md"
    victim.write_text("preserve external content")
    (wiki / "Code-Patterns.md").symlink_to(victim)
    with pytest.raises(RuntimeError, match="ordinary file"):
        build(tmp_path, check=False)
    assert victim.read_text() == "preserve external content"
    assert earlier.read_text() == "preserve earlier page"


@pytest.mark.parametrize("redirected", ["wiki-directory", "bundle-file"])
def test_node_builder_guards_directory_before_generator(tmp_path, redirected):
    """
    The Node entry point rejects redirected wiki roots before invoking Python.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/build_project_wiki.mjs", scripts / "build_project_wiki.mjs")
    victim = tmp_path / "outside"
    victim.mkdir()
    (tmp_path / "docs").mkdir()
    if redirected == "wiki-directory":
        (tmp_path / "docs/wiki").symlink_to(victim, target_is_directory=True)
    else:
        (tmp_path / "docs/wiki").mkdir()
    (tmp_path / "web").mkdir()
    if redirected == "bundle-file":
        (victim / "bundle.json").write_text("preserve bundle victim")
        (tmp_path / "web/project-wiki.json").symlink_to(victim / "bundle.json")
    module = tmp_path / "atlas/code_patterns"
    module.mkdir(parents=True)
    (module.parent / "__init__.py").write_text("")
    (module / "__init__.py").write_text("")
    (module / "wiki.py").write_text("from pathlib import Path\nPath('generator-invoked').write_text('bad')\n")
    result = subprocess.run(["node", str(scripts / "build_project_wiki.mjs")], cwd=tmp_path,
                            env={**os.environ, "ATLAS_WIKI_PYTHON": sys.executable},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "ordinary" in result.stderr
    assert not (tmp_path / "generator-invoked").exists()
    if redirected == "bundle-file":
        assert (victim / "bundle.json").read_text() == "preserve bundle victim"
    else:
        assert list(victim.iterdir()) == []


def test_resource_card_matches_cleanup_failure_policy(catalogue):
    """
    Closing attempts cleanup but a cleanup error can supersede the read error.
    """
    class FailingResource:
        """
        Expose both failure paths without executing documentation source text.
        """

        def read(self):
            """
            Fail before normal completion.
            """
            raise ValueError("read failed")

        def close(self):
            """
            Report a failed cleanup attempt.
            """
            raise RuntimeError("close failed")

    with pytest.raises(RuntimeError, match="close failed") as error:
        with closing(FailingResource()) as resource:
            resource.read()
    assert isinstance(error.value.__context__, ValueError)
    card = catalogue.get("resource-ownership")["pattern"]
    assert "может стать основной" in card["publicContract"]
    assert "попытку cleanup" in card["example"]["explanation"]


def test_dto_card_has_promised_reason_field_in_each_return(catalogue):
    """
    Inspect literal return shapes without executing the example's code.
    """
    card = catalogue.get("versioned-contracts")["pattern"]
    returns = [node for node in ast.walk(ast.parse(card["example"]["code"])) if isinstance(node, ast.Return)]
    assert len(returns) == 2
    for node in returns:
        assert isinstance(node.value, ast.Dict)
        keys = {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
        assert keys == {"version", "status", "value", "reason"}
