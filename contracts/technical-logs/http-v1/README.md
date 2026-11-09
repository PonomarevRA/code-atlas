# Technical logs HTTP v1

Generic local read interface, independent of any consumer. No service references
ag-observer, sends logs to it, or needs it to run. Implementations: Atlas/Python
and AgMemory/.NET. This stream is separate from agent activity.

## Routes and page contract

`GET /api/technical-logs/v1/status` and `GET /api/technical-logs/v1/events`.
Reuse the envelope, cursor validation, status limits, safe errors, strict URL/UTF-8
query parsing, local access rules, no-store and contiguous byte-bound pagination
of `contracts/agent-activity/http-v1`, with these explicit differences:

- envelope schemaVersion: `technical-logs-http/v1`;
- event schemaVersion: `technical-log/v1`;
- events query accepts **only** limit, journalEpoch, afterSequence, beforeSequence;
- no filters at source; consumers filter locally after durable collection;
- default technical journal quota: **100000000 bytes**, configurable separately
  from agent activity. Report actual oldestSequence and droppedTotal: time coverage
  is bounded by traffic and quota and is never guaranteed by a size limit.

100 events and 262144 UTF-8 bytes per page. Sequence strings are canonical signed
nonnegative Int64 decimals. Epoch is required with either exclusive sequence;
before/after cannot be combined. Unknown/repeated parameters return 400. The
default page reads backwards; returned events always ascend. nextCursor is a
continuation, highWatermark is the captured head. Stale cursors return 409 with
resetCursor. Errors and status retain the common shapes and null semantics.

## Technical event

Required keys (nullable fields must be explicit null):

```json
{
  "schemaVersion": "technical-log/v1",
  "eventId": "opaque-id",
  "journalEpoch": "opaque-epoch",
  "sequence": "1",
  "application": "atlas",
  "occurredAt": "2026-10-21T18:00:00Z",
  "recordedAt": "2026-10-21T18:00:00Z",
  "level": "ERROR",
  "category": "service.component",
  "eventName": null,
  "messageTemplate": "Operation failed",
  "message": "Operation failed",
  "exception": {"type": "ValueError", "message": "Invalid value", "stackTrace": "bounded safe stack"},
  "traceId": null,
  "spanId": null,
  "operationId": null,
  "sessionId": null,
  "taskId": null,
  "properties": {}
}
```

application is atlas/agmemory, levels TRACE/DEBUG/INFO/WARN/ERROR/FATAL.
An exception is null or an object with type/message/stackTrace. Maximum serialized
event size 16384 UTF-8 bytes; message up to 2048 characters, safe exception stack up
to 8192 characters, identifiers/category up to128 characters. Implementations may
truncate further to satisfy byte bounds. Never serialize arbitrary logger state,
request bodies, headers, tokens, actor/scope, memory bodies or raw object dumps.
Properties are restricted to safe scalar diagnostic values, with sensitive key
redaction and text redaction. Stack traces contain no local variables.

## Producer behaviour

Native logging integration uses a bounded nonblocking queue and one batch writer.
Count drops; isolate full queues, disk failure and malformed state from application
execution. Do not recursively log journal failures. Exclude technical-read HTTP
access noise to avoid a collector-generated feedback loop. Capture correlation
from existing logging context when available; unknown task IDs remain null.
The application has no observer endpoint, address, SDK or callback configuration.

## Incident consumer

A consumer can preserve two minutes before and after ERROR/FATAL. Continuous
collection is required for useful pre-error coverage. Gaps, late starts, clock
skew, quota eviction and producer drops must be visible. A post-error collection
deadline is fixed to the trigger event (new errors form their own incident windows).
Do not assert root cause from an exception alone; exports contain evidence IDs,
observed facts, gaps and hypotheses for an external agent.
