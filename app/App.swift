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
struct FileAccessReport: Decodable {
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

struct RecoveryStatus: Decodable {
    let config_state: String
    let backups: [BackupRow]
    let desktop_check: String
    let checked_at: Double
}

struct ThreadRow: Identifiable, Decodable {
    let id: String
    let title: String
    let model_provider: String
    let model: String?
    let archived: Int
    let continuation_id: String?
    let continuation_state: String?
    let activity_ms: Int?
}
struct AttentionRow: Identifiable, Decodable {
    let id: String
    let state: String
    let detail: String
}
struct SwitchSummary: Decodable {
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
struct ThreadDiagnosis: Identifiable, Decodable {
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
struct BackupRow: Identifiable, Decodable {
    let id: String
    let created_at: String
    let files: Int
}
struct Preferences: Decodable {
    let keep: Int
    let days: Int
    let auto_cleanup: Bool
}
struct SkippedRepair: Decodable {
    let id: String
    let file: String
    let reason: String
}
struct RestorePreview: Decodable {
    let threads: Int
    let records: Int
    let skipped: [SkippedRepair]
    let error: String?
}
struct SearchState: Decodable {
    let state: String
    let mode: String
    let supported: Bool
    let can_enable: Bool
    let can_disable: Bool
    let requires_restart: Bool
    let description: String
}
struct LiveStatus: Decodable {
    let provider: String
    let model: String
    let effort: String
    let desktop_check: String
    let pending_operation: Bool
    let checked_at: Double
    let web_search: SearchState
}
struct Snapshot: Decodable {
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

    let last_switch: SwitchSummary?
    let ignored_attention: [String]
    let switch_days: Int
    let recent_thread_count: Int
    var liveStatus: LiveStatus {
        LiveStatus(provider:provider,model:model,effort:effort,desktop_check:desktop_check,
                   pending_operation:pending != nil,checked_at:checked_at,web_search:web_search)
    }
}

final class AppModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var live: LiveStatus?
    @Published var liveError: String?
    @Published var refreshInFlight = false
    @Published var busy = false
    @Published var notice = "先查看当前状态，再选择切换目标。"
    @Published var error: String?
    @Published var fileAccess: FileAccessReport?
    @Published var checkingAccess = false
    @Published var accessError: String?
    @Published var recovery: RecoveryStatus?
    @Published var provider = "openai"
    @Published var model = "gpt-6.1-sol"
    @Published var effort = "high"
    @Published var keep = 10
    @Published var days = 30
    @Published var autoCleanup = true
    @Published var tab = 0
    @Published var query = ""
    @Published var showArchived = false
    @Published var restoreID: String? = nil
    @Published var confirmSwitch = false
    @Published var confirmRestore = false
    @Published var confirmRepair = false
    @Published var confirmFormatRepair = false
    @Published var confirmDeepSeekRepair = false
    @Published var confirmSearch = false
    @Published var formatRepairID: String?
    @Published var report = ""
    @Published var diagnostic: ThreadDiagnosis?
    @Published var diagnoses: [String:ThreadDiagnosis] = [:]
    @Published var operationAttention: [AttentionRow] = []
    @Published var onlyAttention = false
    @Published var showPending = false
    @Published var showAdvanced = false
    @Published var showKeySheet = false
    @Published var errorNeedsPermission = false
    @Published var expandedAttentionGroups: Set<String> = []
    @Published var key = ""
    var loaded = false
    var openThread: (String) -> Void = {id in
        if let url = URL(string:"codex://threads/\(id)") {NSWorkspace.shared.open(url)}
    }
    var liveGeneration = 0
    private var accessURLs: [URL] = []
    private let grants = DirectoryGrantStore(defaults:.standard)
    let choices = ["openai":["gpt-6.1-sol","gpt-6-sol","gpt-6-luna","gpt-6-astra"],
                   "deepseek":["deepseek-flash","deepseek-v4-pro"]]
    var attention: [AttentionRow] {
        var rows = snapshot?.last_switch?.attention ?? []
        for row in operationAttention {
            rows.removeAll {$0.id == row.id}; rows.append(row)
        }
        let ignored = Set(snapshot?.ignored_attention ?? [])
        return rows.filter {!ignored.contains($0.id)}
    }
    var ignoredCount: Int {snapshot?.ignored_attention.count ?? 0}
    var attentionTotal: Int {
        let ignored = Set(snapshot?.ignored_attention ?? [])
        return (snapshot?.last_switch?.attention ?? []).filter {!ignored.contains($0.id)}.count
    }
    var attentionGroups: [AttentionGroup] {
        Dictionary(grouping:attention) {$0.state+"|"+$0.detail}
            .map {key,rows in AttentionGroup(id:key,state:rows[0].state,detail:rows[0].detail,rows:rows)}
            .sorted {$0.rows.count > $1.rows.count}
    }
    func stateLabel(_ state: String) -> String {
        switch state {
        case "needs_continuation": return "需要兼容接续"
        case "repairable": return "可修复历史格式"
        case "local_checked": return "本地格式检查通过"
        case "continuation_failed": return "接续待处理"
        default: return "未检查"
        }
    }
    func threadState(_ thread: ThreadRow) -> String {
        if let diagnosis = diagnoses[thread.id] {return stateLabel(diagnosis.state)}
        return stateLabel(attention.first {$0.id == thread.id}?.state ?? "unchecked")
    }
    func diagnose(_ id: String) {
        guard !busy && !checkingAccess else {return}
        busy = true
        notice = "正在读取此会话的本地运行记录和历史格式……"
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                let diagnosis = try JSONDecoder().decode(ThreadDiagnosis.self,from:self.execute("diagnose",["id":id]))
                DispatchQueue.main.async {
                    self.diagnoses[id] = diagnosis; self.diagnostic = diagnosis; self.busy = false
                    self.notice = "诊断完成；只读取本地记录，未发送模型请求。"
                }
            } catch {
                DispatchQueue.main.async {
                    self.error=error.localizedDescription; self.errorNeedsPermission=false; self.busy=false
                }
            }
        }
    }
    var switchTitle: String {
        let days=snapshot?.switch_days ?? 30
        return provider == "deepseek" ? "备份、还原并切换最近 \(days) 天" : "备份并切换最近 \(days) 天"
    }
    var desktopCheck: String {recovery?.desktop_check ?? (liveError == nil ? (live?.desktop_check ?? "unknown") : "unknown")}
    var canManageFiles: Bool {!busy && !checkingAccess && fileAccess?.ready == true}
    var canWrite: Bool {canManageFiles && desktopCheck == "stopped" && live?.pending_operation == false}
    var canRecover: Bool {!busy && !checkingAccess && fileAccess?.canRestore == true && recovery?.desktop_check == "stopped"}
    var enableSearch: Bool {live?.web_search.state == "catalog" || !["live","cached"].contains(live?.web_search.mode ?? "")}
    var providerName: String {
        switch live?.provider {case "deepseek":return "DeepSeek";case "openai":return "GPT";default:return "当前服务商"}
    }
    var providerIcon: NSImage {
        let name = live?.provider == "deepseek" ? "ProviderDeepSeek" : live?.provider == "openai" ? "ProviderGPT" : "AppIcon"
        return NSImage(named:NSImage.Name(name)) ?? NSWorkspace.shared.icon(forFile:Bundle.main.bundlePath)
    }

    init() {
        // Keep user-selected directory grants alive for the app and its Python child.
        accessURLs = grants.restore(resolve:{data in
            var stale = false
            let url = try URL(resolvingBookmarkData:data,options:[.withSecurityScope,.withoutUI],
                              relativeTo:nil,bookmarkDataIsStale:&stale)
            return (url,stale)
        },activate:{$0.startAccessingSecurityScopedResource()},renew:{url in
            try url.bookmarkData(options:.withSecurityScope,includingResourceValuesForKeys:nil,relativeTo:nil)
        })
    }

    deinit {for url in accessURLs {url.stopAccessingSecurityScopedResource()}}

    func checkFileAccess(loadStatus: Bool = false) {
        guard !busy && !checkingAccess else {return}
        checkingAccess = true
        DispatchQueue.global(qos:.utility).async {
            do {
                let report = try JSONDecoder().decode(FileAccessReport.self,from:self.execute("permissions"))
                DispatchQueue.main.async {
                    self.fileAccess = report; self.accessError = nil; self.checkingAccess = false
                    if loadStatus && report.canRestore {self.run(self.loaded ? "status" : "startup")}
                }
            } catch {
                DispatchQueue.main.async {
                    self.fileAccess = nil; self.checkingAccess = false
                    self.accessError = "权限检查无法完成：" + error.localizedDescription
                }
            }
        }
    }

    func requestFolderAccess() {
        guard !busy && !checkingAccess else {return}
        let panel = NSOpenPanel()
        panel.title = "允许切换器访问 Codex 数据目录"
        panel.message = "请选择 .codex 目录或包含它的目录。授权后会重新检查实际读写权限；不会切换模型。"
        panel.prompt = "允许访问"
        panel.canChooseDirectories = true; panel.canChooseFiles = false
        panel.allowsMultipleSelection = false; panel.canCreateDirectories = false
        panel.showsHiddenFiles = true
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex")
        panel.begin {response in
            guard response == .OK, let url = panel.url else {return}
            let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex").standardizedFileURL.path
            let selected = url.standardizedFileURL.path
            guard selected == home || home.hasPrefix(selected + "/") else {
                self.accessError = "所选目录不包含 .codex，请选择 Codex 数据目录或其上级目录。"
                return
            }
            let active = url.startAccessingSecurityScopedResource()
            if active {self.accessURLs.append(url)}
            if let bookmark = try? url.bookmarkData(options:.withSecurityScope,includingResourceValuesForKeys:nil,relativeTo:nil) {
                self.grants.save(bookmark)
            }
            self.checkFileAccess(loadStatus:true)
        }
    }

    func openPermissionSettings() {
        if let url = URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders") {
            NSWorkspace.shared.open(url)
        }
    }

    func refreshLiveStatus() {
        guard loaded && !busy && !refreshInFlight else {return}
        refreshInFlight = true
        let generation = liveGeneration
        DispatchQueue.global(qos:.utility).async {
            do {
                let status = try JSONDecoder().decode(LiveStatus.self,from:self.execute("live-status"))
                DispatchQueue.main.async {
                    self.refreshInFlight = false
                    guard generation == self.liveGeneration else {return}
                    self.live = status; self.liveError = nil
                }
            } catch {
                DispatchQueue.main.async {
                    self.refreshInFlight = false
                    guard generation == self.liveGeneration else {return}
                    self.liveError = "状态读取失败，显示值可能已过期。请点刷新；重新确认前暂停写入。"
                }
            }
        }
    }

    func execute(_ command: String, _ payload: [String:Any] = [:]) throws -> Data {
        let paths = ["/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
                     "/opt/homebrew/bin/python3.13", "/opt/homebrew/bin/python3",
                     "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = paths.first(where: {FileManager.default.isExecutableFile(atPath:$0)}),
              let resource = Bundle.main.resourceURL else {
            throw NSError(domain:"Switcher",code:1,userInfo:[NSLocalizedDescriptionKey:"找不到 Python 3.11+。请按随附使用说明安装。"])
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath:python)
        // Finder may otherwise leave a protected Documents directory as the child's cwd.
        task.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        task.arguments = ["-B", "-s", "-E", resource.appendingPathComponent("backend/cli.py").path, command]
        let output = Pipe(), input = Pipe(), errors = Pipe()
        task.standardOutput = output; task.standardInput = input; task.standardError = errors
        try task.run()
        let bytes = try JSONSerialization.data(withJSONObject:payload)
        input.fileHandleForWriting.write(bytes)
        try input.fileHandleForWriting.close()
        let result = output.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        guard let json = try JSONSerialization.jsonObject(with:result) as? [String:Any] else {
            throw NSError(domain:"Switcher",code:2,userInfo:[NSLocalizedDescriptionKey:"后端未返回有效结果，请检查 Python 版本。"])
        }
        guard json["ok"] as? Bool == true, let value = json["result"] else {
            var info: [String:Any] = [NSLocalizedDescriptionKey:json["error"] as? String ?? "操作失败；请检查文件权限。"]
            if let report = json["permissions"], let data = try? JSONSerialization.data(withJSONObject:report) {
                info["fileAccessReport"] = data
            }
            throw NSError(domain:"Switcher",code:3,userInfo:info)
        }
        return try JSONSerialization.data(withJSONObject:value)
    }

    func readRecovery() -> RecoveryStatus? {
        guard let state = try? JSONDecoder().decode(RecoveryStatus.self,from:execute("recovery-status")),
              ["missing","invalid"].contains(state.config_state) else {return nil}
        return state
    }

    func showRecovery(_ state: RecoveryStatus) {
        recovery = state; snapshot = nil; live = nil; liveError = nil; loaded = false
        diagnoses = [:]; operationAttention = []
        if !state.backups.contains(where: {$0.id == restoreID}) {restoreID = state.backups.first?.id}
        notice = "当前配置缺失或损坏。请选择备份恢复；写入前仍需退出 ChatGPT/Codex。"
    }

    func run(_ command: String, _ payload: [String:Any] = [:]) {
        guard !busy && !checkingAccess else { return }
        busy = true
        liveGeneration += 1
        if command != "status" && command != "startup" { notice = "正在处理，请勿关闭本工具。首次备份可能需要一两分钟……" }
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                var access = try JSONDecoder().decode(FileAccessReport.self,from:self.execute("permissions"))
                guard access.ready || (command == "restore" && access.canRestore) else {
                    let recovery = self.readRecovery()
                    DispatchQueue.main.async {
                        self.fileAccess = access; self.accessError = nil; self.busy = false
                        if let recovery = recovery {self.showRecovery(recovery)}
                        else {self.notice = "文件访问检查未通过，请申请目录访问或打开系统权限设置后重新检查。"}
                    }
                    return
                }
                var message: String?
                var newThread: String?
                var report: String?
                var attention: [AttentionRow]?
                if command != "status" && command != "startup" {
                    let data = try self.execute(command,payload)
                    let result = try JSONSerialization.jsonObject(with:data) as? [String:Any] ?? [:]
                    if result["completed"] as? Bool != false {newThread = result["thread_id"] as? String}
                    if let rows = result["attention"], let data = try? JSONSerialization.data(withJSONObject:rows) {
                        attention = try JSONDecoder().decode([AttentionRow].self,from:data)
                    }
                    if let m = result["message"] as? String {message = m}
                    else if let n = result["migrated"] as? Int {
                        let days=result["scope_days"] as? Int ?? 30
                        message = "已切换最近 \(days) 天内的 \(n) 个会话，更早会话保持原状。"
                    }
                    else if command == "backup" {message = "完整备份已创建并完成自动清理。"}
                    else if let n = result["removed"] as? Int {message = "已清理 \(n) 份过期备份。"}
                    else if command == "ignore-attention" {message = "已忽略此会话；可在待处理列表中恢复。"}
                    else if command == "clear-attention-ignores" {message = "已恢复全部被忽略的会话。"}
                    else {message = "设置已保存。"}
                    if let stats = result["compatibility"] as? [String:Any] {
                        var lines: [String]
                        if result["provider"] as? String == "deepseek" {
                            lines = ["还原 \(stats["threads"] as? Int ?? 0) 个会话、\(stats["records"] as? Int ?? 0) 条原始推理记录。"]
                            if let scanned = stats["scanned"] as? Int {
                                lines.append("检查 \(scanned) 个本机会话；\(stats["unavailable"] as? Int ?? 0) 个缺少本机文件，未检查正文。")
                            }
                        } else {
                            lines = ["检查 \(stats["scanned"] as? Int ?? 0) 个本机会话；修复 \(stats["threads"] as? Int ?? 0) 个会话、\(stats["records"] as? Int ?? 0) 条推理记录。"]
                            let unavailable = stats["unavailable"] as? Int ?? 0
                            if unavailable > 0 {lines.append("\(unavailable) 个会话未找到本机文件，未检查历史格式。")}
                        }
                        for skipped in stats["skipped"] as? [[String:String]] ?? [] {
                            lines.append("跳过 \(skipped["id"] ?? "")：\(skipped["reason"] ?? "未知格式")")
                        }
                        if let backup = result["backup_id"] as? String {lines.append("备份：\(backup)")}
                        report = lines.joined(separator:"\n")
                    }
                }
                if command == "restore" {
                    access = try JSONDecoder().decode(FileAccessReport.self,from:self.execute("permissions"))
                }
                let status = try JSONDecoder().decode(Snapshot.self,from:self.execute(command == "startup" ? "startup" : "status"))
                DispatchQueue.main.async {
                    self.fileAccess = access; self.accessError = nil
                    self.recovery = nil
                    self.snapshot = status
                    self.diagnoses = [:]
                    if command == "switch" {self.operationAttention = []}
                    else if command == "bridge", let id = payload["id"] as? String {
                        self.operationAttention.removeAll {$0.id == id}
                        self.operationAttention.append(contentsOf:attention ?? [])
                    }
                    self.live = status.liveStatus; self.liveError = nil
                    self.keep = status.settings.keep; self.days = status.settings.days
                    self.autoCleanup = status.settings.auto_cleanup
                    if !self.loaded {
                        self.provider = status.provider == "deepseek" ? "deepseek" : "openai"
                        self.model = self.choices[self.provider]!.contains(status.model) ? status.model : self.choices[self.provider]![0]
                        self.loaded = true
                    }
                    if let message = message {self.notice = message}
                    else if command == "startup" {self.notice = "已加载全部历史。写入前先退出 ChatGPT/Codex，再点刷新；完成后重新打开。"}
                    self.busy = false
                    if let report = report {self.report = report}
                    if let tid = newThread {self.openThread(tid)}
                }
            } catch {
                let recovery = self.readRecovery()
                let data = (error as NSError).userInfo["fileAccessReport"] as? Data
                let access = data.flatMap {try? JSONDecoder().decode(FileAccessReport.self,from:$0)}
                    ?? (try? JSONDecoder().decode(FileAccessReport.self,from:self.execute("permissions")))
                DispatchQueue.main.async {
                    self.fileAccess = access
                    let message=error.localizedDescription
                    let clientBlocked=message.contains("请先在 ChatGPT/Codex 中按") ||
                        message.contains("无法确认 ChatGPT") || message.contains("备份后无法确认")
                    self.error = clientBlocked ? nil : message
                    self.errorNeedsPermission = !clientBlocked && (data != nil || message.contains("文件访问"))
                    self.busy = false
                    self.notice = clientBlocked ? message : "操作已停止，请检查提示后重试。"
                    if let recovery = recovery {
                        self.showRecovery(recovery)
                        if command == "startup" || command == "status" {self.error = nil}
                    }
                }
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var app = AppModel()
    var filtered: [ThreadRow] {
        (app.snapshot?.threads ?? []).filter {thread in
            (app.showArchived || thread.archived == 0) && (!app.onlyAttention || app.attention.contains(where: {$0.id == thread.id})) && (app.query.isEmpty || thread.title.localizedCaseInsensitiveContains(app.query) || thread.id.contains(app.query))
        }
    }

    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack(spacing:12) {
                Image(nsImage:app.providerIcon)
                    .resizable().interpolation(.high).frame(width:44,height:44)
                    .accessibilityLabel("当前配置：\(app.providerName) 图标")
                    .help("跟随当前配置中的服务商；下方选择的是下一次切换目标。运行中的客户端可能仍使用启动时配置。")
                VStack(alignment:.leading,spacing:4) {
                    Text("Codex 模型切换器").font(.title2.bold())
                    Text("GPT · DeepSeek · 同一份历史").foregroundStyle(.secondary)
                }
                Spacer()
                Button("刷新",systemImage:"arrow.clockwise") {app.run("status")}
            }
            if let status = app.snapshot {
                HStack(spacing:22) {
                    Label("\(app.live?.model ?? status.model) · \(app.live?.effort ?? status.effort)",systemImage:"cpu")
                    Label("最近 \(status.switch_days) 天 \(status.recent_thread_count) 个",systemImage:"bubble.left.and.bubble.right")
                    Label("\(status.backups.count) 份备份",systemImage:"externaldrive")
                }.font(.callout)
                if app.live?.pending_operation == true {
                    Label("检测到未完成的切换，请到备份页恢复。",systemImage:"exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                if app.desktopCheck == "running" {
                    Label("ChatGPT/Codex 正在运行。写入前请按 ⌘Q 退出并关闭 Codex 后台任务，再点刷新状态。",systemImage:"info.circle")
                        .font(.callout).foregroundStyle(.secondary)
                } else if app.desktopCheck == "unknown" {
                    Label("无法确认 ChatGPT 是否在运行（系统限制了进程查询）。切换前请自行确认已按 ⌘Q 退出。",systemImage:"exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                }
                if let error = app.liveError {Text(error).font(.caption).foregroundStyle(.orange)}
            }
            if let recovery = app.recovery {
                GroupBox("配置应急恢复") {
                    VStack(alignment:.leading,spacing:8) {
                        Text(recovery.config_state == "missing" ? "config.toml 已缺失，仍可从备份恢复。" : "config.toml 无法解析，仍可从备份恢复。")
                            .foregroundStyle(.orange)
                        Text("将从备份重建配置、恢复模型与路由，并保留新增消息。现存损坏配置会单独保留；未备份的设置需手动合并，缺失密钥需重新填写。")
                            .font(.caption).foregroundStyle(.secondary)
                        if recovery.backups.isEmpty {
                            Text("未找到含有效配置的本机备份；请保留现有数据并从原备份手动恢复配置。")
                        } else {
                            HStack {
                                Picker("选择备份",selection:$app.restoreID) {
                                    ForEach(recovery.backups) {backup in
                                        Text(backup.created_at + " · " + backup.id).tag(Optional(backup.id))
                                    }
                                }
                                Button("从备份恢复") {app.confirmRestore = true}
                                    .disabled(!app.canRecover || app.restoreID == nil)
                            }
                        }
                        Button("重新检查") {app.run("status")}.disabled(app.busy || app.checkingAccess)
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }
            }
            TabView(selection:$app.tab) {
                switchView.tabItem {Label("切换模型",systemImage:"arrow.triangle.swap")}.tag(0)
                historyView.tabItem {Label("会话",systemImage:"bubble.left.and.bubble.right")}.tag(1)
                backupView.tabItem {Label("备份",systemImage:"externaldrive")}.tag(2)
                fileAccessView.tabItem {Label("文件权限",systemImage:"lock.shield")}.tag(3)
            }
            Divider()
            if !app.report.isEmpty {
                DisclosureGroup("处理明细") {
                    ScrollView {Text(app.report).font(.caption).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)}
                        .frame(maxHeight:130)
                }
            }
            HStack {
                if app.busy {ProgressView().controlSize(.small)}
                Text(app.notice).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                Button("打开 ChatGPT") {NSWorkspace.shared.open(URL(fileURLWithPath:"/Applications/ChatGPT.app"))}
                    .disabled(app.busy)
            }
        }
        .sheet(item:$app.diagnostic) {diagnosis in
            VStack(alignment:.leading,spacing:14) {
                Text(diagnosis.title.isEmpty ? "会话诊断" : diagnosis.title).font(.title2).lineLimit(2)
                Text(diagnosis.summary).font(.headline)
                Text(diagnosis.evidence)
                Text(diagnosis.format_summary)
                Text("建议：" + diagnosis.advice)
                Text("检查时间：\(Date(timeIntervalSince1970:diagnosis.checked_at).formatted())。这是本地记录快照，未发送模型请求。")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("打开原会话") {if let url = URL(string:"codex://threads/\(diagnosis.id)") {NSWorkspace.shared.open(url)}}
                    Button("兼容接续") {app.diagnostic = nil; app.run("bridge",["id":diagnosis.id])}.disabled(!app.canWrite)
                    Spacer()
                    Button("关闭") {app.diagnostic = nil}.keyboardShortcut(.defaultAction)
                }
            }.padding(24).frame(width:600).textSelection(.enabled)
        }
        .sheet(isPresented:$app.showKeySheet) {
            VStack(alignment:.leading,spacing:14) {
                Text("DeepSeek 密钥").font(.title2.bold())
                Text(app.snapshot?.key_configured == true ? "已保存密钥。只有更换时才需要输入新的。" : "尚未保存密钥。")
                    .foregroundStyle(.secondary)
                SecureField("粘贴 DeepSeek 密钥",text:$app.key)
                    .textFieldStyle(.roundedBorder)
                Text("需要先完全退出 ChatGPT/Codex。保存时会写入钥匙串和当前配置。")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Spacer()
                    Button("关闭") {app.showKeySheet=false}.keyboardShortcut(.cancelAction)
                    Button("保存") {app.run("save-key",["key":app.key]);app.key="";app.showKeySheet=false}
                        .disabled(app.key.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || !app.canWrite)
                        .keyboardShortcut(.defaultAction)
                }
            }.padding(24).frame(width:460)
        }
        .padding(24).frame(minWidth:840,minHeight:660)
        .onAppear {app.run("startup")}
        .alert("操作未完成",isPresented:Binding(get:{app.error != nil},set:{if !$0 {app.error=nil;app.errorNeedsPermission=false}})) {
            if app.errorNeedsPermission {
                Button("申请目录访问…") {app.error=nil; app.errorNeedsPermission=false; app.requestFolderAccess()}
                Button("系统权限设置") {app.error=nil; app.errorNeedsPermission=false; app.openPermissionSettings()}
            }
            Button("知道了",role:.cancel) {app.error=nil; app.errorNeedsPermission=false}
        } message: {Text(app.error ?? "")}
        .confirmationDialog("把最近 \(app.snapshot?.switch_days ?? 30) 天的 GPT／DeepSeek 会话切到 \(app.model)？",isPresented:$app.confirmSwitch,titleVisibility:.visible) {
            Button(app.switchTitle) {app.run("switch",["provider":app.provider,"model":app.model,"effort":app.effort,"days":app.snapshot?.switch_days ?? 30])}
            Button("取消",role:.cancel) {}
        } message: {Text(app.provider == "openai" ? "先备份，再切换并自动修复已验证的 DeepSeek 推理格式。未知格式会话跳过并保留原路由，完成后查看处理明细。先退出 ChatGPT。" : "先备份，再切到 DeepSeek，并还原之前修复过的原始推理记录。修复位置被其他操作改动过的会话会跳过并保留现状。先退出 ChatGPT。")}
        .confirmationDialog("恢复选中的模型、路由与联网设置？",isPresented:$app.confirmRestore,titleVisibility:.visible) {
            Button("创建安全备份并恢复") {if let id=app.restoreID {app.run("restore",["id":id])}}
            Button("取消",role:.cancel) {}
        } message: {Text(app.recovery == nil ? "恢复点之后的新会话和新消息仍会保留。此功能不会用旧数据库覆盖当前历史。" : "当前配置缺失或损坏，将从备份重建配置并恢复模型与路由。损坏文件会单独保留，未备份的设置需手动合并，缺失密钥需重新填写。新增会话和消息仍保留。")}
        .confirmationDialog("只修复当前模型的路由？",isPresented:$app.confirmRepair,titleVisibility:.visible) {
            Button("备份并修复") {app.run("repair")}
            Button("取消",role:.cancel) {}
        } message: {Text("保持当前模型和服务商不变，只把 GPT／DeepSeek 会话的文件头与数据库路由重新对齐。")}
        .confirmationDialog("备份并修复 GPT 历史格式？",isPresented:$app.confirmFormatRepair,titleVisibility:.visible) {
            Button("备份并修复原会话") {
                var payload: [String:Any] = [:]
                if let id = app.formatRepairID {payload["id"] = id}
                app.run("repair-formats",payload)
            }
            Button("取消",role:.cancel) {}
        } message: {Text("保留原会话 ID、正文、附件引用和工具记录，将匹配的 DeepSeek 推理回放项转为历史展示记录。需要先退出 ChatGPT；此功能不发送模型请求。")}
        .confirmationDialog("检查并修复 DeepSeek 历史？",isPresented:$app.confirmDeepSeekRepair,titleVisibility:.visible) {
            Button("备份、还原推理并对齐路由") {
                var payload: [String:Any] = [:]
                if let id = app.formatRepairID {payload["id"] = id}
                app.run("repair-deepseek",payload)
            }
            Button("取消",role:.cancel) {}
        } message: {Text("保留当前 DeepSeek 模型和推理强度。只还原本工具备份过的原始推理区段并对齐路由；冲突会话跳过。未知损坏或缺失的原始推理不能凭空修复。")}
        .confirmationDialog(app.enableSearch ? "开启 DeepSeek 官方内置搜索？" : "关闭 DeepSeek 官方内置搜索？",isPresented:$app.confirmSearch,titleVisibility:.visible) {
            Button(app.enableSearch ? "备份并开启" : "备份并关闭") {app.run("web-search",["enabled":app.enableSearch])}
            Button("取消",role:.cancel) {}
        } message: {Text("只控制当前 DeepSeek 模型的官方内置搜索，是否可用以模型目录声明为准。搜索与模型调用可能产生费用。MCP、浏览器等外部联网工具不受影响。先退出 ChatGPT/Codex，保存后重新打开。")}
    }

    var fileAccessView: some View {
        VStack(alignment:.leading,spacing:0) {
            GroupBox("文件访问权限") {
                VStack(alignment:.leading,spacing:8) {
                HStack {
                    Label(app.checkingAccess ? "正在检查文件读写权限…" : (app.fileAccess?.summary ?? "尚未完成文件访问检查"),
                          systemImage:app.fileAccess?.ready == true && app.accessError == nil ? "checkmark.shield" : "lock.shield")
                        .foregroundStyle(app.fileAccess?.ready == true && app.accessError == nil ? .green : .orange)
                    Spacer()
                    Button("检查权限") {app.checkFileAccess(loadStatus:true)}
                    Button("申请目录访问…") {app.requestFolderAccess()}
                    Button("系统权限设置") {app.openPermissionSettings()}
                }.disabled(app.busy || app.checkingAccess)
                Text("每次操作前实测 .codex 的配置、历史和备份目录；检查不改现有文件，临时文件用于验证写入。若之前拒绝访问，请在系统设置中允许后重新检查；系统提示时需由你确认。")
                    .font(.caption).foregroundStyle(.secondary)
                if let error = app.accessError {Text(error).font(.caption).foregroundStyle(.orange)}
                if let report = app.fileAccess {
                    Text("最近检查：\(Date(timeIntervalSince1970:report.checked_at).formatted(date:.omitted,time:.standard))")
                        .font(.caption).foregroundStyle(.secondary)
                    DisclosureGroup("访问检查明细") {
                        ScrollView {
                            VStack(alignment:.leading,spacing:8) {
                                ForEach(report.checks) {check in
                                    VStack(alignment:.leading,spacing:2) {
                                        Text("\(check.label)：\(check.detail)")
                                            .foregroundStyle(["ok","optional"].contains(check.status) ? Color.secondary : Color.orange)
                                        Text(check.path).textSelection(.enabled).foregroundStyle(.secondary)
                                    }.font(.caption)
                                }
                            }.frame(maxWidth:.infinity,alignment:.leading)
                        }.frame(maxHeight:110)
                    }
                }
                }.padding(6)
            }
            Spacer()
        }
        .padding(20)
        .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
    }

    var switchView: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:16) {
                GroupBox {
                    HStack(spacing:18) {
                        VStack(alignment:.leading,spacing:3) {
                            Text("当前").font(.caption).foregroundStyle(.secondary)
                            Text("\(app.providerName) · \(app.live?.model ?? "读取中")").font(.title3.bold())
                        }
                        Divider().frame(height:30)
                        Label(statusCount, systemImage:"bubble.left.and.bubble.right")
                        Spacer()
                        Button("刷新状态",systemImage:"arrow.clockwise") {app.run("status")}
                            .disabled(app.busy)
                    }.padding(4)
                }

                Text("切换目标").font(.headline)
                Picker("服务商",selection:$app.provider) {
                    Text("GPT · ChatGPT 额度").tag("openai")
                    Text("DeepSeek · API 额度").tag("deepseek")
                }.pickerStyle(.segmented)
                .onChange(of:app.provider) {_,new in app.model=app.choices[new]![0];app.effort="high"}
                HStack(spacing:20) {
                    Picker("模型",selection:$app.model) {
                        ForEach(app.choices[app.provider] ?? [],id:\.self) {Text($0).tag($0)}
                    }
                    Picker("推理",selection:$app.effort) {
                        Text("Low").tag("low")
                        if app.provider == "openai" {Text("Medium").tag("medium")}
                        Text("High").tag("high")
                    }.frame(width:160)
                }

                if app.live?.provider == "deepseek" {
                    HStack {
                        Label(app.live?.web_search.description ?? "正在读取搜索状态……",systemImage:"network")
                            .foregroundStyle(app.live?.web_search.state == "on" ? .green : .secondary)
                        Spacer()
                        Button(app.enableSearch ? "开启搜索" : "关闭搜索") {app.confirmSearch=true}
                            .disabled(!app.canWrite || (app.enableSearch ? app.live?.web_search.can_enable != true : app.live?.web_search.can_disable != true))
                    }
                    Text("官方内置搜索按 models.json 的模型能力启用；Flash 和 V4 Pro 均可由声明决定。")
                        .font(.caption).foregroundStyle(.secondary)
                }

                Button(app.switchTitle,systemImage:"arrow.triangle.swap") {app.confirmSwitch=true}
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .disabled(!app.canWrite)

                if app.provider == "openai" {
                    Label("自动检查已知历史格式问题；无需预先手动修复。",systemImage:"checkmark.shield")
                        .font(.caption).foregroundStyle(.secondary)
                } else if let preview = app.snapshot?.deepseek_restore {
                    if let error = preview.error {
                        Label(error,systemImage:"exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                    } else {
                        Label("待还原 \(preview.threads) 个会话、\(preview.records) 条推理记录",systemImage:"arrow.uturn.backward.circle")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                DisclosureGroup("维护工具",isExpanded:$app.showAdvanced) {
                    VStack(alignment:.leading,spacing:12) {
                        Button("只修复路由",systemImage:"wrench.and.screwdriver") {app.confirmRepair=true}
                            .disabled(!app.canWrite)
                        Button("检查并修复当前模型历史",systemImage:"bandage") {
                            app.formatRepairID=nil
                            if app.live?.provider == "deepseek" {app.confirmDeepSeekRepair=true}
                            else {app.confirmFormatRepair=true}
                        }.disabled(!app.canWrite || !["openai","deepseek"].contains(app.live?.provider ?? ""))
                        Button("DeepSeek 密钥设置",systemImage:"key") {app.showKeySheet=true}
                    }.padding(.top,8)
                }
            }.frame(maxWidth:.infinity,alignment:.leading).padding(20)
        }.disabled(app.busy)
    }

    var statusCount: String {
        guard let status = app.snapshot else {return "读取中"}
        return "最近 \(status.switch_days) 天 \(status.recent_thread_count) 个 · 共 \(status.threads.count) 个"
    }

    var historyView: some View {
        VStack(alignment:.leading,spacing:12) {
            if let summary = app.snapshot?.last_switch {
                GroupBox {
                    VStack(alignment:.leading,spacing:8) {
                        HStack {
                            VStack(alignment:.leading,spacing:2) {
                                Text("上次切换 · \(summary.model)").font(.callout.bold())
                                Text("迁移 \(summary.migrated) · 跳过 \(summary.skipped) · 未检查 \(summary.unavailable)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(app.showPending ? "收起" : "查看待处理 \(app.attention.count)") {
                                app.showPending.toggle()
                            }
                            .disabled(app.attention.isEmpty)
                        }
                        if app.showPending {
                            Divider()
                            ForEach(app.attentionGroups) {group in
                                DisclosureGroup(isExpanded:Binding(
                                    get:{app.expandedAttentionGroups.contains(group.id)},
                                    set:{expanded in
                                        if expanded {app.expandedAttentionGroups.insert(group.id)}
                                        else {app.expandedAttentionGroups.remove(group.id)}
                                    })) {
                                    VStack(alignment:.leading,spacing:6) {
                                        ForEach(Array(group.rows.prefix(10))) {row in
                                            HStack(spacing:10) {
                                                Text(app.snapshot?.threads.first {$0.id == row.id}?.title ?? row.id)
                                                    .lineLimit(1)
                                                Spacer()
                                                Button("诊断") {app.diagnose(row.id)}
                                                    .disabled(app.busy || app.checkingAccess)
                                                Button("忽略") {app.run("ignore-attention",["id":row.id])}
                                                    .disabled(app.busy || app.checkingAccess)
                                            }
                                        }
                                        if group.rows.count > 10 {
                                            Text("此组另 \(group.rows.count-10) 条，请用下方“只看待处理”查看。")
                                                .font(.caption).foregroundStyle(.secondary)
                                        }
                                    }.padding(.top,6).padding(.leading,12)
                                } label: {
                                    HStack(spacing:8) {
                                        Text(app.stateLabel(group.state)).font(.callout.bold())
                                        Text("\(group.rows.count) 条").foregroundStyle(.orange)
                                        Text(group.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }
                            }
                            if app.ignoredCount > 0 {
                                Button("恢复已忽略的 \(app.ignoredCount) 条") {app.run("clear-attention-ignores")}
                                    .font(.caption)
                                    .disabled(app.busy || app.checkingAccess)
                            }
                        }
                    }.padding(4)
                }
            }
            HStack(spacing:12) {
                HStack(spacing:6) {
                    Image(systemName:"magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索标题或完整会话 ID",text:$app.query).textFieldStyle(.plain)
                    if !app.query.isEmpty {
                        Button {app.query = ""} label:{Image(systemName:"xmark.circle.fill")}
                            .buttonStyle(.plain).foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal,9).padding(.vertical,6)
                .background(.quaternary.opacity(0.6),in:RoundedRectangle(cornerRadius:7))
                Toggle("包含已归档",isOn:$app.showArchived).toggleStyle(.checkbox)
                Toggle("只看待处理",isOn:$app.onlyAttention).toggleStyle(.checkbox)
                Text("\(filtered.count) 个匹配").foregroundStyle(.secondary)
            }
            ScrollView {
                LazyVStack(alignment:.leading,spacing:0) {
                    ForEach(filtered) {thread in
                        HStack {
                            Image(systemName:thread.archived == 1 ? "archivebox" : "bubble.left")
                                .foregroundStyle(.secondary)
                            VStack(alignment:.leading,spacing:3) {
                                Text(thread.title.isEmpty ? "未命名会话" : thread.title).lineLimit(1)
                                Text("\(thread.model_provider) · \(thread.model ?? "默认模型") · \(threadDate(thread.activity_ms))")
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                Text(app.threadState(thread)).font(.caption).foregroundStyle(.secondary)
                                if thread.continuation_id != nil {
                                    Text(thread.continuation_state == "verified" ? "接续曾验证成功（以最近运行结果为准）" : "接续尚未验证或需重试")
                                        .font(.caption).foregroundStyle(thread.continuation_state == "verified" ? Color.secondary : Color.orange)
                                }
                            }
                            Spacer()
                            Button("打开") {if let url=URL(string:"codex://threads/\(thread.id)") {NSWorkspace.shared.open(url)}}
                                .buttonStyle(.borderedProminent)
                            Menu {
                                Button("诊断") {app.diagnose(thread.id)}
                                if let tid = thread.continuation_id {
                                    Button("打开接续") {if let url=URL(string:"codex://threads/\(tid)") {NSWorkspace.shared.open(url)}}
                                }
                                Button(thread.continuation_id == nil ? "兼容接续" : "重试接续") {app.run("bridge",["id":thread.id])}
                                    .disabled(!app.canWrite)
                                Button(app.live?.provider == "deepseek" ? "还原推理" : "修复格式") {
                                    app.formatRepairID=thread.id
                                    if app.live?.provider == "deepseek" {app.confirmDeepSeekRepair=true}
                                    else {app.confirmFormatRepair=true}
                                }.disabled(!app.canWrite || !["openai","deepseek"].contains(app.live?.provider ?? ""))
                                Button("忽略此会话") {app.run("ignore-attention",["id":thread.id])}
                                Divider()
                                Button("复制 ID") {NSPasteboard.general.clearContents();NSPasteboard.general.setString(thread.id,forType:.string)}
                            } label: {
                                Image(systemName:"ellipsis.circle")
                            }
                            .menuStyle(.borderlessButton)
                            .frame(width:26)
                        }.padding(.vertical,7)
                        Divider()
                    }
                }
            }.frame(maxHeight:.infinity)
            Text("搜索会同时匹配标题和完整会话 ID。诊断只读本地记录；兼容接续需要先退出 ChatGPT。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(20)
        .frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topLeading)
    }

    func threadDate(_ value: Int?) -> String {
        guard let value=value, value>0 else {return "时间未知"}
        return Date(timeIntervalSince1970:Double(value)/1000)
            .formatted(date:.abbreviated,time:.shortened)
    }

    var backupView: some View {
        VStack(alignment:.leading,spacing:16) {
            HStack(spacing:20) {
                Toggle("自动清理",isOn:$app.autoCleanup).toggleStyle(.checkbox)
                Stepper("最近 \(app.keep) 份",value:$app.keep,in:3...100).frame(width:160)
                Stepper("最长 \(app.days) 天",value:$app.days,in:1...3650).frame(width:170)
                Button("保存规则") {app.run("settings",["keep":app.keep,"days":app.days,"auto_cleanup":app.autoCleanup])}
                    .disabled(!app.canManageFiles)
            }
            Text("始终保留最近 3 份成功备份。自动清理在本工具打开时、成功备份后执行；关闭后无后台任务。相同内容只保存一份。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("立即备份",systemImage:"plus.circle") {app.run("backup")}.disabled(!app.canWrite)
                Button("立即清理",systemImage:"trash") {app.run("cleanup")}
                    .disabled(!app.canManageFiles)
                Button("打开备份文件夹",systemImage:"folder") {
                    if let path=app.snapshot?.backup_path {NSWorkspace.shared.open(URL(fileURLWithPath:path))}
                }
                Spacer()
            }
            List(selection:$app.restoreID) {
                ForEach(app.snapshot?.backups ?? []) {b in
                    VStack(alignment:.leading,spacing:3) {
                        Text(b.created_at).font(.callout)
                        Text("\(b.files) 个文件 · \(b.id)").font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical,4).tag(b.id)
                }
            }.listStyle(.inset)
            HStack {
                Text("恢复回退模型、路由与联网设置；新增消息保留，完整快照留作人工恢复。")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("恢复选中备份") {app.confirmRestore=true}
                    .disabled(app.restoreID == nil || !app.canManageFiles || app.desktopCheck != "stopped")
            }
        }.padding(20).disabled(app.busy)
    }
}

#if !SWITCHER_MODEL_CHECKS
@main
struct ProviderSwitcherApp: App {
    var body: some Scene {
        WindowGroup {ContentView()}
            .defaultSize(width:940,height:700)
    }
}
#endif
