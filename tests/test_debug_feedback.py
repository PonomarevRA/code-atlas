"""Untrusted JUnit input stays bounded, reported and separate from captured source facts."""

from dataclasses import FrozenInstanceError
import json

import pytest

from atlas.debug_feedback import FeedbackError, parse_junit_report, build_feedback_package


def report(body, snapshot="s", build="b"):
    """
    Import an isolated testcase fixture without executing the reported tests.
    """
    return parse_junit_report(("<testsuite>" + body + "</testsuite>").encode(), "r", snapshot, build)


def fact(**updates):
    """
    Supply a small host-captured fixture; no path is opened by the package builder.
    """
    return {"id": "node-1", "file": "tests/test_a.py", "line": 4, "snapshotId": "s",
            "sourceHash": "captured-hash", "name": "test_a", **updates}


def failure(file="tests/test_a.py"):
    """
    Build a small reported failure with optional unsafe file text for rejection tests.
    """
    return f'<testcase name="test_a" file="{file}" line="4"><failure>trace</failure></testcase>'


def test_statuses_reported_counts_immutability_and_no_output_logs():
    """
    Status fields distinguish passing, failure, error, skipped and conflicting outcomes.
    """
    value = report('<testcase name="a"/><testcase name="b"><failure>bad</failure></testcase>'
                   '<testcase name="c"><error>err</error></testcase><testcase name="d"><skipped/></testcase>'
                   '<testcase name="e"><failure/><skipped/></testcase><system-out>do-not-include</system-out>')
    assert value.summary() == {"passed": 1, "failure": 1, "error": 1, "skipped": 1, "unknown": 1}
    assert value.to_dict()["executionVerified"] is False
    assert "do-not-include" not in json.dumps(value.to_dict())
    with pytest.raises(FrozenInstanceError):
        value.snapshot_id = "forged"
    data = value.to_dict()
    data["cases"].clear()
    assert len(value.cases) == 5
    with pytest.raises(FeedbackError, match="immutable"):
        build_feedback_package(data, "s", [])


@pytest.mark.parametrize("data", [
    b'<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite/>',
    b'<!DOCTYPE testsuite SYSTEM "file:///etc/passwd"><testsuite/>',
    b'<!DOCTYPE testsuite [<!ENTITY x SYSTEM "https://example.org/">]><testsuite/>',
    '<!DOCTYPE testsuite [<!ENTITY x "a">]><testsuite/>'.encode("utf-16"),
    '<!DOCTYPE testsuite [<!ENTITY x "a">]><testsuite/>'.encode("utf-16-le"),
    b'<?xml version="1.0" encoding="UTF-16"?><testsuite/>',
    b'<testsuite>\x00</testsuite>',
])
def test_dtd_entities_and_encoding_bypasses_rejected(data):
    """
    Unsupported declarations/encodings cannot bypass a raw-byte string search.
    """
    with pytest.raises(FeedbackError) as error:
        parse_junit_report(data, "r")
    assert error.value.code in {"unsafe_xml", "unsupported_encoding"}


@pytest.mark.parametrize("data", [b"", b"<testsuite>", b"<unrelated/>"])
def test_malformed_or_unsupported_report(data):
    """
    Invalid XML is not normalized into an empty successful report.
    """
    with pytest.raises(FeedbackError):
        parse_junit_report(data, "r")


def test_structural_input_and_output_limits():
    """
    Limit bytes and testcase count before retaining unbounded report state.
    """
    with pytest.raises(FeedbackError) as error:
        parse_junit_report(b"x" * (1024 * 1024 + 1), "r")
    assert error.value.status == 413
    with pytest.raises(FeedbackError, match="Too many"):
        report('<testcase name="x"/>' * 513)
    with pytest.raises(FeedbackError, match="structure"):
        report("<nested>" * 33 + "</nested>" * 33)
    value = report(''.join(f'<testcase name="f{i}"><failure>{"X" * 3000}</failure></testcase>' for i in range(30)))
    package = build_feedback_package(value, "s", [], "b")
    assert len(package["items"]) == 20
    assert package["truncated"]
    assert len(json.dumps(package, ensure_ascii=False).encode()) <= 64000
    assert max(len(case.details) for case in value.cases) <= 2048
    assert sum(len(case.details) for case in value.cases) <= 32768


def test_exact_static_source_candidate_and_provenance():
    """
    Exact paths/lines find captured candidates without proving runtime binding.
    """
    package = build_feedback_package(report(failure()), "s", [fact()], "b")
    item = package["items"][0]
    assert item["sourceLinkStatus"] == "matched-to-captured-source"
    assert item["sourceRefs"][0]["sourceHash"] == "captured-hash"
    assert package["executionVerified"] is False
    assert "caller-supplied" in package["identityProvenance"]


@pytest.mark.parametrize("snapshot,build,expected", [
    (None, "b", "report-snapshot-unknown"), ("old", "b", "report-snapshot-stale"),
    ("s", "old", "report-build-stale"),
])
def test_stale_or_missing_identity_does_not_link(snapshot, build, expected):
    """
    Matching source paths do not override missing or mismatched report identities.
    """
    package = build_feedback_package(report(failure(), snapshot, build), "s", [fact()], "b")
    assert expected in package["unknownReasons"]
    assert not package["items"][0]["sourceRefs"]


@pytest.mark.parametrize("path", ["../secret.py", "/tmp/secret.py", "C:\\secret.py", "a/../../secret.py"])
def test_unsafe_report_paths_do_not_link_or_echo(path):
    """
    No source read or path-based match is attempted for escaping report paths.
    """
    package = build_feedback_package(report(failure(path)), "s", [fact()], "b")
    item = package["items"][0]
    assert item["pathStatus"] == "invalid"
    assert item["file"] is None and not item["sourceRefs"]


def test_missing_source_hash_duplicate_reports_and_candidates_remain_unknown():
    """
    Ambiguous identities and unavailable captured hashes are not invented or merged.
    """
    package = build_feedback_package(report(failure()), "s", [fact(sourceHash=None)], "b")
    assert "source-hash-unknown" in package["items"][0]["unknownReasons"]
    duplicate = build_feedback_package(report(failure() + failure()), "s", [fact()], "b")
    assert all("ambiguous-test-identity" in item["unknownReasons"] for item in duplicate["items"])
    assert all(not item["sourceRefs"] for item in duplicate["items"])
    candidates = build_feedback_package(report(failure()), "s", [fact(), fact(id="node-2")], "b")
    assert "source-not-unique" in candidates["items"][0]["unknownReasons"]


def test_unique_captured_names_are_only_candidates():
    """
    Name-only linking requires exact captured class and name rather than guessing.
    """
    value = report('<testcase name="test_a" classname="Suite"><failure/></testcase>')
    assert not build_feedback_package(value, "s", [fact()], "b")["items"][0]["sourceRefs"]
    assert build_feedback_package(value, "s", [fact(className="Suite")], "b")["items"][0]["sourceRefs"]


def test_empty_report_not_passing_and_common_credentials_masked():
    """
    Missing testcases remain unknown; diagnostic text is bounded and best-effort redacted.
    """
    empty = build_feedback_package(report(""), "s", [], "b")
    assert not empty["resultsPresent"] and "no-testcases" in empty["unknownReasons"]
    value = report('<testcase name="x"><failure>Bearer abc123 password=hunter2</failure></testcase>')
    text = value.cases[0].details
    assert "abc123" not in text and "hunter2" not in text
    assert "[redacted]" in text


def test_namespace_and_nested_output_do_not_expose_logs():
    """
    Namespaced reports retain failure text but omit even misplaced nested logs.
    """
    xml = b'<testsuite xmlns="urn:junit"><testcase name="x"><failure>before<system-out>private-log</system-out>after</failure></testcase></testsuite>'
    value = parse_junit_report(xml, "r")
    assert "before" in value.cases[0].details and "after" in value.cases[0].details
    assert "private-log" not in value.cases[0].details


def test_boolean_source_line_is_not_a_line_identity():
    """
    Python's bool/int equality cannot bind a malformed captured line to line one.
    """
    value = report(failure().replace('line="4"', 'line="1"'))
    result = build_feedback_package(value, "s", [fact(line=True)], "b")
    assert not result["items"][0]["sourceRefs"]


def test_utf8_response_budget_omits_whole_cases_with_explicit_truncation():
    """
    Wide Unicode diagnostics and identities cannot exceed the serialized byte cap.
    """
    cases = []
    facts = []
    for index in range(20):
        name = str(index) + "я" * 480
        path = f"tests/{index}.py"
        cases.append(f'<testcase name="{name}" classname="{"к" * 500}" file="{path}" line="4"><failure>{"ж" * 1800}</failure></testcase>')
        facts.append(fact(id=str(index) + "н" * 480, file=path))
    value = build_feedback_package(report("".join(cases)), "s", facts, "b")
    assert value["truncated"] and 0 < len(value["items"]) < 20
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 64000
    assert all(item["sourceRefs"] for item in value["items"])


@pytest.mark.parametrize("body", [
    '<system-out><testcase name="log-text"/></system-out>',
    '<properties><testcase name="property"/></properties>',
    '<system-err><testsuite><testcase name="wrapped-log"/></testsuite></system-err>',
    '<properties><testsuite><testcase name="wrapped-property"/></testsuite></properties>',
])
def test_embedded_log_or_property_elements_cannot_create_results(body):
    """
    Only testcases in the suite-container tree are imported as actual cases.
    """
    with pytest.raises(FeedbackError) as error:
        report(body)
    assert error.value.code == "unsupported_report"


@pytest.mark.parametrize("attributes", [
    'status="notrun"', 'status="suppressed"', 'status="run"',
    'result="notrun"', 'result="suppressed"', 'result="completed"',
    'status=""', 'status="run" result="suppressed"',
])
def test_extended_outcome_profiles_never_become_implicit_pass(attributes):
    """
    Unsupported producer outcome attributes are unknown, even when names suggest success.
    """
    value = report(f'<testcase name="x" {attributes}/>')
    assert value.summary()["passed"] == 0 and value.summary()["unknown"] == 1
    package = build_feedback_package(value, "s", [], "b")
    assert package["items"][0]["status"] == "unknown"
    assert "unsupported-outcome-attributes" in package["items"][0]["unknownReasons"]


def test_classic_nested_suite_results_and_escaped_log_text():
    """
    Valid suite hierarchy and escaped log payloads remain supported without fake results.
    """
    xml = b'<testsuites><testsuite><testsuite><testcase name="real"/></testsuite><system-out>&lt;testcase name="fake"/&gt;</system-out></testsuite></testsuites>'
    value = parse_junit_report(xml, "r")
    assert len(value.cases) == 1 and value.cases[0].name == "real"


def test_redaction_expansion_respects_retained_case_output_limit():
    """
    Masking short repeated tokens can expand text and must set truncation truthfully.
    """
    raw = ("api_key=x " * 195).strip()
    assert len(raw) < 2048
    value = report(f'<testcase name="x"><failure>{raw}</failure></testcase>')
    case = value.cases[0]
    assert len(case.details) <= 2048
    assert case.details_truncated
    assert "api_key=x" not in case.details


def test_redaction_expansion_respects_total_retained_report_limit():
    """
    Individually small diagnostics cannot exceed the report-wide output quota after masking.
    """
    raw = ("api_key=x " * 16).strip()
    value = report("".join(f'<testcase name="{index}"><failure>{raw}</failure></testcase>' for index in range(200)))
    assert sum(len(case.details) for case in value.cases) <= 32768
    assert max(len(case.details) for case in value.cases) <= 2048
    assert any(case.details_truncated for case in value.cases)
