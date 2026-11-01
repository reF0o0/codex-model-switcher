import SwiftUI
import AppKit

#if !SWITCHER_MODEL_CHECKS
@main
struct SwitcherLauncher {
    @MainActor
    static func main() {
        if CommandLine.arguments.contains("--preview") {PreviewDriver.launch()}
        else {ProviderSwitcherApp.main()}
    }
}
struct ProviderSwitcherApp: App {
    var body: some Scene {
        WindowGroup {ContentView()}
            .defaultSize(width:940,height:700)
    }
}
#endif
