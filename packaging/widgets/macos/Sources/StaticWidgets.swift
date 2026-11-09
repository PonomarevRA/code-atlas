import WidgetKit
import SwiftUI

/// Basic real WidgetKit provider: fixed service identity/default port, with no AppIntent metadata dependency.
struct StaticProvider: TimelineProvider {
    let application: AGApplication
    func placeholder(in context: Context) -> Entry {Entry(date:.now, application:application.rawValue, snapshot:nil, error:"No snapshot loaded")}
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {Task {completion(await load())}}
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        Task {completion(Timeline(entries:[await load()],policy:.after(.now.addingTimeInterval(300))))}
    }
    /// Never start a producer or substitute demonstration data when its fixed loopback endpoint is unavailable.
    private func load() async -> Entry {
        do {return Entry(date:.now,application:application.rawValue,snapshot:try await fetchSnapshot(application:application,port:nil),error:nil)}
        catch {return Entry(date:.now,application:application.rawValue,snapshot:nil,error:"Offline or invalid snapshot; basic widget uses preferred port")}
    }
}
/// Each service has its own zero-argument Widget type and stable OS kind.
func basicConfiguration(_ application: AGApplication) -> some WidgetConfiguration {
    StaticConfiguration(kind:"ag.desktop.requests."+application.rawValue,provider:StaticProvider(application:application)) {entry in RequestsView(entry:entry)}
        .configurationDisplayName(application.rawValue+" requests")
        .description("A budgeted snapshot from this service's preferred loopback port. Unknown clients and offline states are explicit.")
        .supportedFamilies([.systemSmall,.systemMedium,.systemLarge])
}
struct AtlasRequests: Widget { var body: some WidgetConfiguration {basicConfiguration(.atlas)} }
struct MemoryRequests: Widget { var body: some WidgetConfiguration {basicConfiguration(.memory)} }
struct ObserverRequests: Widget { var body: some WidgetConfiguration {basicConfiguration(.observer)} }
struct TasksRequests: Widget { var body: some WidgetConfiguration {basicConfiguration(.tasks)} }
struct DesignRequests: Widget { var body: some WidgetConfiguration {basicConfiguration(.design)} }
@main struct BasicWidgets: WidgetBundle {
    var body: some Widget {
        AtlasRequests()
        MemoryRequests()
        ObserverRequests()
        TasksRequests()
        DesignRequests()
    }
}
