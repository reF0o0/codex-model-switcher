import SwiftUI
import AppKit

enum RecoveryCheckError: Error {case failed(String)}

@main
struct RecoveryModelChecks {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() {throw RecoveryCheckError.failed(message)}
    }
    static func wait(_ predicate: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !predicate() && Date() < deadline {RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
        try require(predicate(), "async update timed out")
    }
    @MainActor
    static func main() throws {
        _ = NSApplication.shared
        let resources = Bundle.main.resourceURL!
        let home = URL(fileURLWithPath:try String(contentsOf:resources.appendingPathComponent("fixture-home.txt"),encoding:.utf8))
        let config = home.appendingPathComponent("config.toml")
        let desktop = home.appendingPathComponent("desktop-state.txt")
        let app = AppModel()
        app.openThread = {_ in fatalError("recovery must not open a conversation")}
        app.run("startup"); try wait {!app.busy}
        try require(app.recovery?.config_state == "missing", "missing config did not enter recovery")
        try require(app.recovery?.backups.count == 1 && app.restoreID != nil, "valid backup missing")
        try require(app.canRecover && !app.canWrite, "recovery should be available without enabling normal writes")

        try "running".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("status"); try wait {!app.busy}
        try require(!app.canRecover, "running client must disable recovery")
        try "stopped".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("status"); try wait {!app.busy}
        app.run("restore",["id":app.restoreID!]); try wait {!app.busy}
        try require(app.error == nil && app.recovery == nil && app.canWrite, "missing-config restore did not recover normal state")
        try require(app.snapshot?.threads.count == 2, "restore lost thread rows")
        let restored = try String(contentsOf:config,encoding:.utf8)
        try require(!restored.contains("<stored-in-keychain>"), "placeholder credential restored")
        try "running".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("switch",["provider":"openai","model":"gpt-6.1-sol","effort":"high","days":30]); try wait {!app.busy}
        try require(app.error == nil && app.notice.contains("ChatGPT/Codex"), "running-client error should be a notice without permission actions")
        try "stopped".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("status"); try wait {!app.busy}

        let damaged = Data([0xff,0xfe,0x31])
        try damaged.write(to:config)
        app.run("status"); try wait {!app.busy}
        try require(app.error == nil && app.recovery?.config_state == "invalid", "invalid UTF8 did not enter recovery")
        let database = home.appendingPathComponent("state_5.sqlite")
        try FileManager.default.setAttributes([.posixPermissions:0o400],ofItemAtPath:database.path)
        defer {try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:database.path)}
        app.run("status"); try wait {!app.busy}
        try require(!app.canRecover, "unwritable database must disable recovery")
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:database.path)
        app.run("status"); try wait {!app.busy}
        app.run("restore",["id":app.restoreID!]); try wait {!app.busy}
        try require(app.error == nil && app.recovery == nil && app.canWrite, "corrupt-config restore did not recover normal state")
        let saved = try FileManager.default.contentsOfDirectory(at:home.appendingPathComponent("provider-switcher/config-recovery"),includingPropertiesForKeys:nil)
        try require(saved.count == 1, "damaged config was not retained")
        let retained = try Data(contentsOf:saved[0])
        try require(retained == damaged, "damaged config bytes changed")

        try Data("not a sqlite database\n".utf8).write(to:database)
        app.run("status"); try wait {!app.busy}
        try require(app.recovery?.databaseBad == true, "corrupt database did not enter recovery")
        try require(app.databaseRestoreID != nil, "database recovery backup missing")
        app.run("recover-database",["id":app.databaseRestoreID!]); try wait {!app.busy}
        try require(app.error == nil && app.recovery == nil && app.canWrite,
                    "database recovery did not restore normal state")
        try require(app.snapshot?.threads.count == 2, "database recovery lost thread rows")
        app.run("switch",["provider":"openai","model":"gpt-6.1-sol","effort":"high","days":30]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "on" && !app.enableSearch,
                    "GPT must default to live search and offer disabling")
        app.run("web-search",["enabled":false]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "off" && app.enableSearch,
                    "GPT search toggle did not persist through CLI and refresh")
        app.run("web-search",["enabled":true]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "on", "GPT search could not be reenabled")
        try "running".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("status"); try wait {!app.busy}
        app.run("web-search",["enabled":false]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "on" && !app.canWrite,
                    "running-client guard must preserve GPT search configuration")
        try "stopped".write(to:desktop,atomically:true,encoding:.utf8)
        app.run("switch",["provider":"deepseek","model":"deepseek-v4-pro","effort":"high","days":30]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "on", "DeepSeek Pro must default to live search")
        app.run("web-search",["enabled":false]); try wait {!app.busy}
        app.run("switch",["provider":"openai","model":"gpt-6.1-sol","effort":"high","days":30]); try wait {!app.busy}
        try require(app.live?.web_search.state == "on", "DeepSeek off must not disable GPT search")
        app.run("switch",["provider":"deepseek","model":"deepseek-flash","effort":"high","days":30]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "unsupported" && app.live?.web_search.can_enable == false,
                    "Flash must report the server-side native-search limitation")
        app.run("switch",["provider":"deepseek","model":"deepseek-v4-pro","effort":"high","days":30]); try wait {!app.busy}
        try require(app.error == nil && app.live?.web_search.state == "off", "Flash visit must preserve DeepSeek's explicit off preference")
        try Data("{\"models\":[{\"slug\":\"gpt-6.2-sol\",\"visibility\":\"list\",\"supported_reasoning_levels\":[{\"effort\":\"high\"}]},{\"slug\":\"gpt-6.1-terra\",\"visibility\":\"list\",\"supported_reasoning_levels\":[{\"effort\":\"high\"},{\"effort\":\"max\"}]},{\"slug\":\"gpt-reserve\",\"visibility\":\"hide\"},{\"slug\":\"gpt-6-luna\",\"visibility\":\"list\",\"supported_reasoning_levels\":[{\"effort\":\"high\"}]}]}".utf8)
            .write(to:home.appendingPathComponent("models_cache.json"))
        let snapshotDate = app.snapshot!.checked_at
        app.refreshLiveStatus(); try wait {!app.refreshInFlight}
        try require(app.snapshot!.checked_at == snapshotDate && app.live!.checked_at > snapshotDate,
                    "light refresh must update live state without replacing history snapshot")
        try require(app.choices["openai"] == ["gpt-6.2-sol", "gpt-6.1-terra", "gpt-6-luna"], "visible catalog choices and order must match")
        app.provider = "openai"; app.model = "gpt-6.1-terra"; app.effort = "max"; app.normalizeTarget()
        try require(app.effortChoices.contains("max") && app.effort == "max", "catalog reasoning levels missing")
        app.model = "gpt-6-luna"; app.normalizeTarget()
        try require(app.effort == "high", "unsupported target effort must reset")
        try require(app.snapshot?.history_checked == false, "UI status must not scan history on refresh")
        app.defaultOnly = true
        app.provider = "openai"; app.model = "gpt-6.1-terra"; app.effort = "max"
        let originalProviders = app.snapshot!.threads.map(\.model_provider)
        app.prepareSwitch(); try wait {!app.preparingSwitch}
        try require(app.switchPreview?.candidate_threads == 0 && app.switchPreview?.migrate == false,
                    "default-only preview should not migrate any session")
        try require(app.switchPreviewText.contains("gpt-6.1-terra") && app.confirmSwitch,
                    "confirmation must show the actual prepared target")
        app.performPreparedSwitch(); try wait {!app.busy}
        try require(app.error == nil && app.live?.model == "gpt-6.1-terra",
                    "default-only switch failed through UI and CLI")
        try require(app.snapshot!.threads.map(\.model_provider) == originalProviders,
                    "default-only operation changed historical providers")
        print("Fast startup and default-only preview checks: PASS")
        print("Model catalog and lightweight refresh AppModel checks: PASS")
        print("Search AppModel checks: PASS (GPT/Pro defaults, toggles, CLI refresh, client guard, independent preferences, Flash limitation)")
        print("Recovery AppModel checks: PASS (missing/invalid config, backup selection, client guard, file permission gate, restoration, retained damage, database recovery, normal state)")
    }
}
