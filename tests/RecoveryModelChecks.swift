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
        print("Recovery AppModel checks: PASS (missing/invalid config, backup selection, client guard, file permission gate, restoration, retained damage, database recovery, normal state)")
    }
}
