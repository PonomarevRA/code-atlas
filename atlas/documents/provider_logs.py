"""Immutable log framing and fresh local Drain3 mining; no live tail or commands."""

import hashlib
import json
import math
import re
from pathlib import Path

from .models import TreeBuilder
from .office_package import provider_fingerprint


class StructuredLogsProvider:
    """Keep source-byte/line spans and distinguish strict JSONL from envelopes."""

    id = "logs-structured"
    version = "1"
    capabilities = {"jsonl": True, "explicitMultiline": True, "liveTail": False, "runtimeCallGraph": False}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__)

    def parse(self, path: Path, options: dict, context: dict):
        """Frame UTF-8 captured bytes; retain malformed/truncated event evidence."""
        if options.get("redacted_only"):
            raise ValueError("logs_redacted_only_requires_upstream_redacted_capture")
        maximum = int(options.get("max_source_bytes", 100 * 1024 * 1024))
        if path.stat().st_size > maximum:
            raise ValueError("logs_source_byte_limit")
        encoding = options.get("encoding", "utf-8")
        if encoding != "utf-8":
            raise ValueError("logs_only_utf8_supported")
        payload = path.read_bytes()
        payload.decode("utf-8", errors="strict")
        generation = hashlib.sha256(payload).hexdigest()
        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        root = builder.add("section", builder.root_id, "Source generation", attributes={"sourceGeneration": generation, "encoding": encoding, "capturedRepresentation": "original"})
        framing = options.get("framing", "jsonl" if path.suffix.lower() in {".jsonl", ".ndjson"} else "lines")
        if framing not in {"jsonl", "lines", "envelope"}:
            raise ValueError("logs_unknown_framing")
        reasons = []
        for event in self._events(payload, framing, options):
            status, raw, fields, first, last, start, end = event
            message = fields.get("message", fields.get("msg", fields.get("log", raw)))
            if not isinstance(message, str):
                message = json.dumps(message, ensure_ascii=False, allow_nan=False)
            locator = {"type": "log", "precision": "exact", "sourceGeneration": generation, "byteStart": start, "byteEnd": end, "byteEndExclusive": True, "lineStart": first, "lineEnd": last, "lineBasis": 1, "encoding": encoding, "capturedRepresentation": "original"}
            attributes = {"role": "event", "status": status, "framing": framing}
            for target, names in {"timestamp": ("timestamp", "time", "@timestamp"), "severity": ("severity", "level"), "traceId": ("traceId", "trace_id"), "spanId": ("spanId", "span_id")}.items():
                for name in names:
                    if name in fields:
                        value = fields[name]
                        if isinstance(value, (dict, list)) or len(str(value).encode("utf-8")) > 1024:
                            attributes[target + "Status"] = "captured_field"
                        else:
                            attributes[target] = value
                        break
            if "timestamp" in attributes:
                attributes["timestampInterpretation"] = "source_lexeme_unverified"
            node = builder.add("item", root, f"Event {first}", message, locator, attributes)
            builder.add("code", node, "Captured envelope", raw, locator, {"role": "raw_envelope"})
            for key, value in fields.items():
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                field_attributes = {"valueType": type(value).__name__}
                if len(encoded.encode("utf-8")) <= 1024:
                    field_attributes["value"] = value
                else:
                    field_attributes["valueStatus"] = "captured_content"
                    field_attributes["contentEncoding"] = "json"
                field_locator = {**locator, "precision": "structural", "spanScope": "event"}
                builder.add("key", node, str(key)[:160], encoded, field_locator, field_attributes)
            if status != "complete":
                reasons.append(status)
        return builder.build({"status": "partial" if reasons else "complete", "reasonCodes": sorted(set(reasons)), "framing": framing, "sourceGeneration": generation, "runtimeCallGraph": False})

    def _events(self, payload, framing, options):
        """Frame bytes first so UTF-8 characters cannot corrupt source offsets."""
        offset = 0
        current = None
        pattern = None
        if framing == "envelope":
            expression = options.get("start_pattern")
            if not isinstance(expression, str) or not expression or len(expression) > 1024:
                raise ValueError("logs_envelope_requires_start_pattern")
            pattern = re.compile(expression)
        for number, line in enumerate(payload.splitlines(keepends=True), 1):
            end = offset + len(line)
            raw = line.decode("utf-8").rstrip("\r\n")
            if framing == "jsonl":
                status = "complete"
                fields = {}
                try:
                    parsed = json.loads(raw, parse_constant=self._reject_constant, object_pairs_hook=self._unique_object)
                    fields = parsed if isinstance(parsed, dict) else {"value": parsed}
                    self._check_numbers(fields)
                except (ValueError, RecursionError):
                    fields = {}
                    status = "invalid_jsonl" if line.endswith((b"\n", b"\r")) else "truncated_final_record"
                yield status, raw, fields, number, number, offset, end
            elif framing == "lines":
                yield "complete", raw, {"message": raw}, number, number, offset, end
            else:
                match = pattern.match(raw)
                if match is not None:
                    if current is not None:
                        yield tuple(current)
                    fields = {key: value for key, value in match.groupdict().items() if value is not None}
                    current = ["complete", raw, fields, number, number, offset, end]
                elif current is None:
                    current = ["orphan_continuation", raw, {}, number, number, offset, end]
                else:
                    current[1] += "\n" + raw
                    current[4], current[6] = number, end
                    if "message" in current[2]:
                        current[2]["message"] += "\n" + raw
            offset = end
        if current is not None:
            if options.get("eof_policy", "complete") == "incomplete":
                current[0] = "incomplete_eof_event"
            yield tuple(current)

    @staticmethod
    def _reject_constant(value):
        """Strict JSON disallows NaN and Infinity accepted by Python's default."""
        raise ValueError("invalid_json_constant: " + value)

    @staticmethod
    def _unique_object(pairs):
        """Reject duplicate fields instead of silently dropping their evidence."""
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    @staticmethod
    def _check_numbers(value, depth=0):
        """Reject overflowing exponents and bound structured-field recursion."""
        if depth > 128:
            raise ValueError("logs_field_depth_limit")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("logs_nonfinite_number")
        if isinstance(value, (list, dict)):
            items = value.values() if isinstance(value, dict) else value
            for item in items:
                StructuredLogsProvider._check_numbers(item, depth + 1)


class Drain3LogsProvider(StructuredLogsProvider):
    """Mine chosen event messages in fixed source order from an empty miner."""

    id = "logs-drain3"
    capabilities = {**StructuredLogsProvider.capabilities, "templateMining": True, "initialState": "empty"}

    @property
    def runtime_fingerprint(self):
        return provider_fingerprint(__file__, "drain3")

    def parse(self, path: Path, options: dict, context: dict):
        """Persist final templates and event assignments without reusable checkpoints."""
        from drain3 import TemplateMiner
        from drain3.template_miner_config import TemplateMinerConfig

        if options.get("checkpoint") or options.get("resume"):
            raise ValueError("drain3_checkpoint_resume_unsupported")
        tree = super().parse(path, options, context)
        configuration = TemplateMinerConfig()
        configuration.drain_max_clusters = int(options.get("max_clusters", 1000))
        configuration.drain_depth = int(options.get("drain_depth", 4))
        configuration.drain_max_children = int(options.get("max_children", 100))
        configuration.drain_sim_th = float(options.get("similarity_threshold", 0.4))
        if not 1 <= configuration.drain_max_clusters <= 10000 or not 3 <= configuration.drain_depth <= 128 or not 1 <= configuration.drain_max_children <= 1000 or not 0 <= configuration.drain_sim_th <= 1:
            raise ValueError("drain3_invalid_configuration")
        miner = TemplateMiner(persistence_handler=None, config=configuration)
        groups = {}
        occurrences = {}
        for node in tree["nodes"]:
            if node["attributes"].get("role") != "event" or node["attributes"]["status"] != "complete":
                continue
            assignment = miner.add_log_message(node["text"])
            cluster_id = assignment["cluster_id"]
            node["attributes"]["clusterId"] = cluster_id
            groups[cluster_id] = assignment["template_mined"]
            occurrences.setdefault(cluster_id, []).append(node["id"])
        # Old clusters may have been evicted. Their last observed templates remain
        # valid evidence for the persisted assignments and are never relabelled.
        retained = {cluster.cluster_id for cluster in miner.drain.clusters}
        builder = TreeBuilder(path.name, max_nodes=int(options.get("max_nodes", 100000)), max_text_bytes=int(options.get("max_text_bytes", 16 * 1024 * 1024)))
        builder.nodes = tree["nodes"]
        builder.edges = tree["edges"]
        builder.warnings = tree["warnings"]
        builder.text_bytes = sum(len(item["text"].encode("utf-8")) + len(item["label"].encode("utf-8")) for item in builder.nodes)
        for cluster_id, template in groups.items():
            group = builder.add("section", builder.root_id, f"Template {cluster_id}", template, attributes={"role": "template", "clusterId": cluster_id, "evicted": cluster_id not in retained, "method": "drain3", "initialState": "empty"})
            for event_id in occurrences[cluster_id]:
                builder.edges.append({"source": group, "target": event_id, "relation": "template_occurrence", "method": "drain3", "certainty": "observed_assignment"})
        coverage = dict(tree["coverage"])
        configuration_data = {"maxClusters": configuration.drain_max_clusters, "depth": configuration.drain_depth, "maxChildren": configuration.drain_max_children, "similarityThreshold": configuration.drain_sim_th, "numericTokens": configuration.parametrize_numeric_tokens}
        configuration_hash = hashlib.sha256(json.dumps(configuration_data, sort_keys=True).encode("utf-8")).hexdigest()
        coverage.update({"miningInitialState": "empty", "trainingOrder": "captured_source_order", "masking": "none", "checkpointLoaded": False, "configuration": configuration_data, "configurationHash": configuration_hash})
        if len(retained) < len(groups):
            coverage["status"] = "partial"
            coverage["reasonCodes"] = sorted(set(coverage["reasonCodes"] + ["drain3_cluster_eviction"]))
        return builder.build(coverage)


class LnavLogsProvider:
    """Read captured logs with a bundled parser-only lnav pack and fixed SQL."""

    id = "logs-lnav"
    version = "1"
    capabilities = {"requiresCertifiedParserOnlyPack": True, "runtimeInstalls": False,
                    "nativeFormats": True, "externalCommands": False, "runtimeCallGraph": False}

    @property
    def runtime_fingerprint(self):
        from atlas.lnav_runtime import parser_fingerprint
        return provider_fingerprint(__file__) + ':' + parser_fingerprint()

    def parse(self, path: Path, options: dict, context: dict):
        """Keep normalized event facts without claiming exact physical byte locations."""
        from atlas.lnav_runtime import parse_log
        if options.get('redacted_only'):
            raise ValueError('logs_redacted_only_requires_upstream_redacted_capture')
        allowed = {'max_events', 'timeout_seconds', 'max_output_bytes', 'memory_bytes', 'max_nodes', 'max_text_bytes'}
        if set(options) - allowed:
            raise ValueError('lnav_unknown_option')
        result = parse_log(path, **{key: options[key] for key in allowed - {'max_nodes', 'max_text_bytes'} if key in options})
        with path.open('rb') as input_file:
            generation = hashlib.file_digest(input_file, 'sha256').hexdigest()
        builder = TreeBuilder(path.name, max_nodes=int(options.get('max_nodes', 100000)),
                              max_text_bytes=int(options.get('max_text_bytes', 16 * 1024 * 1024)))
        for row in result['rows']:
            locator = {'type': 'log', 'precision': 'structural', 'sourceGeneration': generation,
                       'logLine': row.get('log_line'), 'lineBasis': 0, 'lineUnit': 'lnav_virtual_log_line',
                       'capturedRepresentation': 'original'}
            attributes = {'role': 'event', 'parser': 'lnav', 'severity': row.get('log_level'),
                          'timestamp': row.get('log_time'), 'timestampInterpretation': 'lnav_normalized'}
            label = str(row.get('log_level') or 'Event') + ' ' + str(row.get('log_time') or '')
            raw = str(row.get('log_text') or '')
            body = str(row.get('log_body') or raw)
            event = builder.add('item', builder.root_id, label, body, locator, attributes)
            if raw:
                builder.add('code', event, 'Captured log record', raw, locator, {'role': 'raw_envelope'})
        reasons = ['lnav_event_limit'] if result['truncated'] else []
        if not result['rows']:
            reasons.append('lnav_no_recognized_events')
        status = 'unsupported' if not result['rows'] else 'partial' if reasons else 'complete'
        return builder.build({'status': status, 'reasonCodes': reasons, 'sourceGeneration': generation,
                              'runtimeFingerprint': result['runtimeFingerprint'], 'externalCommandsExecuted': False,
                              'runtimeCallGraph': False, 'locationPrecision': 'structural'})
