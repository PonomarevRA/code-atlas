# Agent activity v1

Portable browser contract for Atlas and AgMemory. Producer internals and disk
formats remain application-owned; authority and storage paths never cross this
boundary. JSON names use camelCase and sequence numbers are decimal strings.

## UI defaults

The activity mini-panel and graph markers are two independent, default-off
preferences. Adjacent controls at the corner launcher enable each consumer.
Enabling one does not enable the other. Closing or minimizing a panel does not
stop backend recording. A page uses one shared stream while any live consumer
is enabled. No input field invokes a model.

## REST

- `GET /api/agent-activity/status`: `schemaVersion`, `journalEpoch`, `maxSizeBytes`,
  `usedBytes`, `oldestSequence`, `latestSequence`, `health`, `cursor`.
- `GET /api/agent-activity/history`: optional `projectId`, `sessionId`, `operation`,
  `q`, `beforeSequence`, `limit` (maximum 100). Returns `events`, `cursor`,
  `oldestSequence`, `hasMore`, `resetRequired`.
- `POST /api/agent-activity/clear`, JSON `{}`: clears only activity history and
  changes journalEpoch. Returns current status.

AgMemory applies its fixed configured authority before filtering; these inputs
never select an actor, scope or storage directory. Search text is a local query
over the permitted journal projection, not a new model/tool call.

## WebSocket

`/api/agent-activity/stream`, optional Atlas `projectId` query parameter.
Client subscribes with `{type:"subscribe",cursor?:{journalEpoch,sequence}}`.
Server emits snapshot/events packets with `messageId`, `journalEpoch`, `cursor`,
`events`, and optional `health`/`omittedCount`. Each packet is at most 128 events
and 64 KiB including its envelope. A snapshot is a bounded recent window;
omissions are explicit and full history is paginated through REST.

Client applies a packet to its bounded reducer and sends
`{type:"ack",messageId,cursor}`. V1 uses stop-and-wait: one outstanding packet,
at most 64 KiB, with a 5-second progress timeout. This is stricter than the
planned 256 KiB credit window and avoids a separate sliding-window implementation.
ACK is accepted only for the actual outstanding packet in this subscription.

For an unavailable cursor the server sends
`{type:"resetRequired",reason,journalEpoch}` and closes with 4009. The client
restarts without the stale cursor. Normal transient disconnects resume from the
last applied cursor. Disable/unmount closes the connection and reconnect timer.

## Events and positions

See `event.schema.json` and `examples.json`. Event metadata describes observed
operations, never private reasoning or unobserved terminal/editor activity.
`identityQuality=process` must be shown as a session, not a confirmed subagent.
Null/missing target is normal for searches without a selected result.

Atlas resolves code target nodeId/projectId/snapshotId against the current scene.
AgMemory resolves only authorized opaque node keys for the current graph
generation; durable memory IDs are not a browser target. A stale, unavailable or
out-of-view target remains explicit; coordinates are never fabricated.

Query policy is preview/fingerprint/omitted. The new AgMemory journal captures
only explicitly routed search previews (up to 256 characters, sanitized) when
`ActivityJournal.CaptureQueryPreview` is enabled. Other payloads remain omitted.
The existing privacy-limited usage audit and navigation visit log are separate.

## Request and response cards

Group events by `(application, sessionId, operationId)`. One operation card shows
`request.tool` and `request.summary`, followed by the observed `response.summary`.
Started without finished means awaiting an observed response, never success.
History may begin after the started event was rotated out; show the retained
request summary on finished rather than inventing missing events.

`response.items` contains at most three real labels and optional file/nodeId,
each at most 128 characters. `response.resultCount` describes the actual returned
count/total when known; missing means unknown. `response.truncated` explicitly
marks an incomplete preview. Errors and cancellations remain explicit. Summaries
are generated from the existing request/response, without model calls or repeat
operations. Full code and memory record bodies are excluded from this projection.
