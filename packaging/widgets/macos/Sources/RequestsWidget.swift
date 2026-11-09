import AppIntents
import SwiftUI
import WidgetKit

/// Public WidgetKit configuration exposes fixed application choices and an optional actual port only.
enum ApplicationChoice: String, AppEnum {
    case atlas, memory, observer, tasks, design
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Application")
    static var caseDisplayRepresentations: [Self: DisplayRepresentation] = [
        .atlas:"Code Atlas", .memory:"AgMemory", .observer:"ag-observer", .tasks:"ag-tasks", .design:"ag-design"]
    var application: AGApplication { switch self { case .atlas: .atlas; case .memory: .memory; case .observer: .observer; case .tasks: .tasks; case .design: .design } }
}

struct RequestsConfiguration: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "AG requests"
    static var description = IntentDescription("Read a bounded snapshot from an existing local service. No service is launched.")
    @Parameter(title: "Application", default: .design) var application: ApplicationChoice
    @Parameter(title: "Actual port (optional)") var port: Int?
}

/// Request one bounded local snapshot when WidgetKit permits a refresh; never start another service.
struct Provider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: .now, application: "AG requests", snapshot: nil, error: "No snapshot loaded") }
    func snapshot(for configuration: RequestsConfiguration, in context: Context) async -> Entry { await load(configuration) }
    func timeline(for configuration: RequestsConfiguration, in context: Context) async -> Timeline<Entry> {
        // WidgetKit owns its budget; this is a requested refresh time, never a real-time guarantee.
        Timeline(entries: [await load(configuration)], policy: .after(.now.addingTimeInterval(300)))
    }
    private func load(_ configuration: RequestsConfiguration) async -> Entry {
        do {
            let value = try await fetchSnapshot(application: configuration.application.application, port: configuration.port)
            return Entry(date: .now, application: value.appId, snapshot: value, error: nil)
        } catch { return Entry(date: .now, application: configuration.application.application.rawValue, snapshot: nil, error: "Offline or invalid snapshot") }
    }
}

@main struct AGRequestsWidgets: WidgetBundle {
    var body: some Widget {
        AGRequestsWidget()
    }
}
struct AGRequestsWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "ag.desktop.requests", intent: RequestsConfiguration.self, provider: Provider()) { entry in RequestsView(entry: entry) }
            .configurationDisplayName("AG agent requests")
            .description("A budgeted snapshot of actual local operations; unknown clients are labeled explicitly.")
            .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}
