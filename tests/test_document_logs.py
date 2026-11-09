"""Logs preserve immutable evidence, explicit framing and deterministic mining."""

import hashlib
import json
from unittest.mock import patch

import pytest

from atlas.documents.models import DocumentError
from atlas.documents.provider_logs import Drain3LogsProvider, LnavLogsProvider, StructuredLogsProvider


def _events(tree):
    """Select event nodes independently of template or structured-field nodes."""
    return [node for node in tree["nodes"] if node["attributes"].get("role") == "event"]


def test_strict_jsonl_unicode_byte_spans_structured_fields_and_final_newline(tmp_path):
    """Unicode bytes, source generations and timestamps retain their actual units."""
    path = tmp_path / "events.jsonl"
    lines = [json.dumps({"message": "Привет", "timestamp": "10:30", "traceId": "t1"}, ensure_ascii=False), '{"message":"EOF"}']
    payload = (lines[0] + "\r\n" + lines[1]).encode("utf-8")
    path.write_bytes(payload)
    tree = StructuredLogsProvider().parse(path, {}, {})
    first, last = _events(tree)
    assert tree["coverage"]["status"] == "complete"
    assert first["text"] == "Привет"
    locator = first["sourceLocator"]
    assert payload[locator["byteStart"]:locator["byteEnd"]] == (lines[0] + "\r\n").encode("utf-8")
    assert last["sourceLocator"]["byteStart"] == len((lines[0] + "\r\n").encode("utf-8"))
    assert locator["sourceGeneration"] == hashlib.sha256(payload).hexdigest()
    assert first["attributes"]["timestamp"] == "10:30"
    assert first["attributes"]["timestampInterpretation"] == "source_lexeme_unverified"
    assert tree["edges"] == []
    assert tree["coverage"]["runtimeCallGraph"] is False


@pytest.mark.parametrize("invalid", ["", "{\"a\":1} {\"b\":2}", "{\"value\":NaN}", "{\"value\":1e9999}", "{\"a\":1,\"a\":2}", "\ufeff{}"])
def test_jsonl_strict_profile_rejects_blank_multiple_nan_duplicate_and_bom(tmp_path, invalid):
    """A permissive JSON stream is never mislabeled strict JSONL."""
    path = tmp_path / "strict.jsonl"
    path.write_text(invalid + "\n", encoding="utf-8")
    tree = StructuredLogsProvider().parse(path, {}, {})
    assert tree["coverage"]["status"] == "partial"
    assert _events(tree)[0]["attributes"]["status"] == "invalid_jsonl"


def test_jsonl_truncated_last_event_retains_source_evidence(tmp_path):
    """A malformed EOF record remains visible with a distinct status and byte span."""
    path = tmp_path / "truncated.jsonl"
    payload = b'{"message":"ok"}\n{"message":'
    path.write_bytes(payload)
    tree = StructuredLogsProvider().parse(path, {}, {})
    assert [node["attributes"]["status"] for node in _events(tree)] == ["complete", "truncated_final_record"]
    assert _events(tree)[1]["sourceLocator"]["byteEnd"] == len(payload)


def test_envelope_multiline_orphan_and_explicit_eof_policy(tmp_path):
    """Only a selected envelope pattern joins stack continuation lines."""
    path = tmp_path / "service.log"
    payload = "orphan\n10:30 ERROR failed\n  at Service.Run\n10:31 INFO ready".encode()
    path.write_bytes(payload)
    options = {"framing": "envelope", "start_pattern": r"(?P<timestamp>\d{2}:\d{2}) (?P<severity>\w+) (?P<message>.*)", "eof_policy": "incomplete"}
    tree = StructuredLogsProvider().parse(path, options, {})
    events = _events(tree)
    assert [node["attributes"]["status"] for node in events] == ["orphan_continuation", "complete", "incomplete_eof_event"]
    assert events[1]["text"] == "failed\n  at Service.Run"
    locator = events[1]["sourceLocator"]
    assert locator["lineStart"] == 2 and locator["lineEnd"] == 3
    assert payload[locator["byteStart"]:locator["byteEnd"]] == b"10:30 ERROR failed\n  at Service.Run\n"
    with pytest.raises(ValueError, match="requires_start_pattern"):
        StructuredLogsProvider().parse(path, {"framing": "envelope"}, {})


def test_plain_lines_remain_separate_without_selected_multiline_profile(tmp_path):
    """Default text framing makes no inferred timestamp or multiline promises."""
    path = tmp_path / "plain.log"
    path.write_text("ERROR failed\n at Service.Run\n")
    tree = StructuredLogsProvider().parse(path, {}, {})
    assert len(_events(tree)) == 2
    assert all("timestamp" not in node["attributes"] for node in _events(tree))


def test_drain3_fresh_build_assignments_templates_and_config_are_deterministic(tmp_path):
    """Independent builds start empty and preserve actual assignment edges."""
    path = tmp_path / "messages.jsonl"
    path.write_text("\n".join(json.dumps({"message": text}) for text in ["user 1 logged in", "user 2 logged in", "user 3 logged in", "disk failed"]) + "\n")
    provider = Drain3LogsProvider()
    first = provider.parse(path, {}, {})
    second = provider.parse(path, {}, {})
    assert first == second
    events = _events(first)
    assert events[0]["attributes"]["clusterId"] == events[1]["attributes"]["clusterId"]
    templates = [node for node in first["nodes"] if node["attributes"].get("role") == "template"]
    assert len(templates) == 2
    assert len(first["edges"]) == 4
    assert {edge["relation"] for edge in first["edges"]} == {"template_occurrence"}
    assert first["coverage"]["miningInitialState"] == "empty"
    assert first["coverage"]["checkpointLoaded"] is False
    assert len(first["coverage"]["configurationHash"]) == 64
    with pytest.raises(ValueError, match="checkpoint_resume"):
        provider.parse(path, {"checkpoint": "untrusted.jsonpickle"}, {})


def test_drain3_eviction_keeps_assignments_to_last_observed_template(tmp_path):
    """Evicted groups remain persisted and coverage reflects bounded clustering."""
    path = tmp_path / "eviction.log"
    path.write_text("alpha alpha alpha\nbeta beta beta\ngamma gamma gamma\n")
    tree = Drain3LogsProvider().parse(path, {"max_clusters": 1, "similarity_threshold": 1}, {})
    assert len(_events(tree)) == 3
    assert len(tree["edges"]) == 3
    assert "drain3_cluster_eviction" in tree["coverage"]["reasonCodes"]
    assert any(node["attributes"].get("evicted") for node in tree["nodes"])


def test_lnav_missing_or_uncertified_binary_never_executes_commands(tmp_path):
    """A PATH executable cannot substitute a missing certified bundled parser pack."""
    from atlas.documents.models import DocumentError
    path = tmp_path / "service.log"
    path.write_text("sample\n")
    missing = DocumentError('CAPABILITY_UNAVAILABLE', 'fixture pack unavailable', 409)
    with patch("shutil.which", return_value="/untrusted/lnav"), \
         patch("atlas.lnav_runtime._checked_pack", side_effect=missing), \
         patch("subprocess.Popen", side_effect=AssertionError("command executed")):
        with pytest.raises(DocumentError) as error:
            LnavLogsProvider().parse(path, {}, {})
        assert error.value.code == 'CAPABILITY_UNAVAILABLE'


def test_logs_reject_budget_overflow_and_unimplemented_redacted_capture(tmp_path):
    """Redacted-only cannot be promised while the store captures the original blob."""
    path = tmp_path / "service.log"
    path.write_text("secret-value\n")
    provider = StructuredLogsProvider()
    with pytest.raises(ValueError, match="redacted_capture"):
        provider.parse(path, {"redacted_only": True}, {})
    with pytest.raises(ValueError, match="byte_limit"):
        provider.parse(path, {"max_source_bytes": 1}, {})
    with pytest.raises(DocumentError, match="узлов"):
        provider.parse(path, {"max_nodes": 2}, {})
