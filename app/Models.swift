import SwiftUI
import AppKit

struct DirectoryGrantStore {
    static let key = "directory-access-bookmarks-v1"
    let defaults: UserDefaults

    func save(_ bookmark: Data) {
        var bookmarks = defaults.array(forKey:Self.key) as? [Data] ?? []
        if !bookmarks.contains(bookmark) {bookmarks.append(bookmark)}
        defaults.set(bookmarks,forKey:Self.key)
    }

    func restore(resolve: (Data) throws -> (URL,Bool), activate: (URL) -> Bool,
                 renew: (URL) throws -> Data) -> [URL] {
        let bookmarks = defaults.array(forKey:Self.key) as? [Data] ?? []
        var updated = bookmarks
        var active: [URL] = []
        for (index,data) in bookmarks.enumerated() {
            guard let (url,stale) = try? resolve(data), activate(url) else {continue}
            active.append(url)
            if stale, let renewed = try? renew(url) {updated[index] = renewed}
        }
        if updated != bookmarks {defaults.set(updated,forKey:Self.key)}
        return active
    }
}

struct FileAccessCheck: Decodable, Identifiable {
    let label: String
    let path: String
    let status: String
    let detail: String
    let files_checked: Int
    var id: String {label + path}
}
struct FileAccessReport: Decodable, Sendable {
    let ready: Bool
    let checked_at: Double
    let summary: String
    let checks: [FileAccessCheck]
    var canRestore: Bool {
        checks.allSatisfy {row in
            ["ok","optional"].contains(row.status) ||
            (row.label == "配置文件" && row.status == "missing" && row.path.hasSuffix("/config.toml"))
        }
    }
}

struct RecoveryStatus: Decodable, Sendable {
    let config_state: String
    let backups: [BackupRow]
    let database_state: [String:String]?
    let database_backups: [BackupRow]?
    let unreadable_backups: Int?
    let desktop_check: String
    let checked_at: Double
}
extension RecoveryStatus {
    var configBad: Bool {["missing","invalid"].contains(config_state)}
    var databaseBad: Bool {
        let primary = database_state?["state_5.sqlite"]
        let history = database_state?["thread_history_1.sqlite"]
        return ["invalid","missing"].contains(primary ?? "") || history == "invalid"
    }
}

struct ThreadRow: Identifiable, Decodable, Sendable {
    let id: String
    let title: String
    let model_provider: String
    let model: String?
    let archived: Int
    let continuation_id: String?
    let continuation_state: String?
    let activity_ms: Int?
}
struct AttentionRow: Identifiable, Decodable, Sendable {
    let id: String
    let state: String
    let detail: String
}
struct SwitchSummary: Decodable, Sendable {
    let provider: String
    let model: String
    let migrated: Int
    let skipped: Int
    let unavailable: Int
    let scope_days: Int?
    let attention: [AttentionRow]
    let checked_at: Double
}
struct AttentionGroup: Identifiable {
    let id: String
    let state: String
    let detail: String
    let rows: [AttentionRow]
}
struct ThreadDiagnosis: Identifiable, Decodable, Sendable {
    let id: String
    let title: String
    let state: String
    let category: String
    let summary: String
    let evidence: String
    let advice: String
    let format_summary: String
    let checked_at: Double
}
struct BackupRow: Identifiable, Decodable, Sendable {
    let scope: String?
    let id: String
    let created_at: String
    let files: Int
}
struct Preferences: Decodable, Sendable {
    let keep: Int
    let days: Int
    let auto_cleanup: Bool
}
struct SkippedRepair: Decodable, Sendable {
    let id: String
    let file: String
    let reason: String
}
struct RestorePreview: Decodable, Sendable {
    let threads: Int
    let records: Int
    let skipped: [SkippedRepair]
    let error: String?
}
struct SearchState: Decodable, Sendable {
    let state: String
    let mode: String
    let supported: Bool
    let can_enable: Bool
    let can_disable: Bool
    let requires_restart: Bool
    let description: String
}
struct ModelOption: Decodable, Sendable {
    let slug: String
    let efforts: [String]
}
struct LiveStatus: Decodable, Sendable {
    let provider: String
    let model: String
    let effort: String
    let desktop_check: String
    let pending_operation: Bool
    let checked_at: Double
    let web_search: SearchState
    let model_options: [String:[ModelOption]]?
}
struct SwitchPreview: Decodable, Sendable {
    let current_provider: String
    let current_model: String
    let provider: String
    let model: String
    let effort: String
    let migrate: Bool
    let scope_days: Int
    let candidate_threads: Int
    let web_search: String
    let message: String
}

struct Snapshot: Decodable, Sendable {
    let history_checked: Bool?
    let provider: String
    let model: String
    let effort: String
    let threads: [ThreadRow]
    let backups: [BackupRow]
    let settings: Preferences
    let desktop_running: Bool
    let desktop_check: String
    let pending: [String:String]?
    let backup_path: String
    let key_configured: Bool
    let key_storage: String?
    let deepseek_restore: RestorePreview?
    let web_search: SearchState
    let checked_at: Double
    let warnings: [String]?
    let pending_error: Bool?

    let model_options: [String:[ModelOption]]?
    let last_switch: SwitchSummary?
    let ignored_attention: [String]
    let switch_days: Int
    let recent_thread_count: Int
    var liveStatus: LiveStatus {
        LiveStatus(provider:provider,model:model,effort:effort,desktop_check:desktop_check,
                   pending_operation:pending != nil || pending_error == true,
                   checked_at:checked_at,web_search:web_search,model_options:model_options)
    }
}

