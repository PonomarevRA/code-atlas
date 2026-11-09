import SwiftUI
import WidgetKit

struct Entry: TimelineEntry {
    let date: Date
    let application: String
    let snapshot: Snapshot?
    let error: String?
}

struct RequestsView: View {
    let entry: Entry
    @Environment(\.widgetFamily) private var family
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(entry.application).font(.headline)
            if let value = entry.snapshot {
                Text(value.status + ((value.captureDate ?? .distantPast).timeIntervalSinceNow < -300 ? " · stale" : ""))
                    .font(.caption).foregroundStyle(.secondary)
                if value.requests.isEmpty { Text("No observed requests in this process").font(.caption) }
                ForEach(Array(value.requests.suffix(family == .systemSmall ? 1 : 3).enumerated()), id: \.offset) { _, row in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(row.operation + " · " + row.status).font(.caption).bold().lineLimit(1)
                        Text(row.actorLabel).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        Text(row.requestSummary).font(.caption2).lineLimit(1)
                        Text(row.responseSummary).font(.caption2).lineLimit(1)
                    }
                }
                Text("Captured " + value.capturedAt).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            } else { Text(entry.error ?? "No snapshot").font(.caption) }
            Spacer(minLength: 0)
        }.frame(maxWidth: .infinity, alignment: .leading).containerBackground(.fill.tertiary, for: .widget)
    }
}

