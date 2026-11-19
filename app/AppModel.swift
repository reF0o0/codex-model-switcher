import SwiftUI
import AppKit

@MainActor
final class AppModel: ObservableObject {
    nonisolated let backend = BackendClient()
    let isPreview: Bool
    @Published var defaultOnly = false
    @Published var switchPreview: SwitchPreview?
    @Published var preparingSwitch = false
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
    @Published var model = "gpt-6.2-sol"
    @Published var effort = "high"
    @Published var keep = 10
    @Published var days = 30
    @Published var switchDays = 30
    @Published var autoCleanup = true
    @Published var tab = 0
    @Published var query = ""
    @Published var showArchived = false
    @Published var restoreID: String? = nil
    @Published var databaseRestoreID: String? = nil
    @Published var confirmSwitch = false
    @Published var confirmRestore = false
    @Published var confirmDatabaseRestore = false
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
    @Published var keySaved = false
    var loaded = false
    var openThread: (String) -> Void = {id in
        if let url = URL(string:"codex://threads/\(id)") {NSWorkspace.shared.open(url)}
    }
    var liveGeneration = 0
    private var accessURLs: [URL] = []
    private let grants = DirectoryGrantStore(defaults:.standard)
    var choices: [String:[String]] {
        guard let options = live?.model_options else {
            return ["openai":["gpt-6.2-sol","gpt-6.2-astra","gpt-6.1-terra","gpt-6.1-sol",
                              "gpt-6-astra","gpt-6-sol","gpt-6-luna","gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna"],
                    "deepseek":["deepseek-flash","deepseek-v4-pro"]]
        }
        return options.mapValues {$0.map(\.slug)}
    }
    var effortChoices: [String] {
        live?.model_options?[provider]?.first {$0.slug == model}?.efforts ??
            (provider == "openai" ? ["low","medium","high"] : ["low","high"])
    }
    func normalizeTarget() {
        if !(choices[provider] ?? []).contains(model) {model = choices[provider]?.first ?? ""}
        if !effortChoices.contains(effort) {effort = effortChoices.contains("high") ? "high" : effortChoices.first ?? "high"}
    }
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
        if isPreview {return}
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
        if defaultOnly {return "备份并修改默认模型"}
        return provider == "deepseek" ? "备份、还原并切换最近 \(switchDays) 天" : "备份并切换最近 \(switchDays) 天"
    }
    var desktopCheck: String {recovery?.desktop_check ?? (liveError == nil ? (live?.desktop_check ?? "unknown") : "unknown")}
    var canManageFiles: Bool {!busy && !checkingAccess && !preparingSwitch && fileAccess?.ready == true}
    var canWrite: Bool {canManageFiles && desktopCheck == "stopped" && live?.pending_operation == false}
    var canRecover: Bool {!busy && !checkingAccess && fileAccess?.canRestore == true && recovery?.desktop_check == "stopped"}
    var canRecoverDatabase: Bool {canRecover}
    var enableSearch: Bool {live?.web_search.state == "catalog" || !["live","cached","indexed"].contains(live?.web_search.mode ?? "")}
    var providerName: String {
        switch live?.provider {case "deepseek":return "DeepSeek";case "openai":return "GPT";default:return "当前服务商"}
    }
    var providerIcon: NSImage {
        let name = live?.provider == "deepseek" ? "ProviderDeepSeek" : live?.provider == "openai" ? "ProviderGPT" : "AppIcon"
        return NSImage(named:NSImage.Name(name)) ?? NSWorkspace.shared.icon(forFile:Bundle.main.bundlePath)
    }

    init(preview:Bool = false) {
        isPreview = preview
        if preview {loadPreview(); return}
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
        if isPreview {return}
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
        if isPreview {return}
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
        if isPreview {return}
        guard loaded && !busy && !refreshInFlight else {return}
        refreshInFlight = true
        let generation = liveGeneration
        DispatchQueue.global(qos:.utility).async {
            do {
                let status = try JSONDecoder().decode(LiveStatus.self,from:self.execute("live-status"))
                DispatchQueue.main.async {
                    self.refreshInFlight = false
                    guard generation == self.liveGeneration else {return}
                    self.live = status; self.liveError = nil; self.normalizeTarget()
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

    nonisolated func execute(_ command:String, _ payload:[String:Any] = [:], input:Data? = nil) throws -> Data {
        try backend.execute(command,input:input ?? JSONSerialization.data(withJSONObject:payload))
    }

    nonisolated func readRecovery() -> RecoveryStatus? {
        guard let state = try? JSONDecoder().decode(RecoveryStatus.self,from:execute("recovery-status")) else {return nil}
        let configBad = ["missing","invalid"].contains(state.config_state)
        let databaseBad = state.databaseBad
        return configBad || databaseBad ? state : nil
    }

    func showRecovery(_ state: RecoveryStatus) {
        recovery = state; snapshot = nil; live = nil; liveError = nil; loaded = false
        diagnoses = [:]; operationAttention = []
        if !state.backups.contains(where: {$0.id == restoreID}) {restoreID = state.backups.first?.id}
        if !(state.database_backups ?? []).contains(where: {$0.id == databaseRestoreID}) {
            databaseRestoreID = state.database_backups?.first?.id
        }
        let configBad = ["missing","invalid"].contains(state.config_state)
        let databaseBad = state.databaseBad
        notice = databaseBad && !configBad ? "数据库无法通过完整性检查。请选择含有效数据库的备份；写入前仍需退出 ChatGPT/Codex。"
                 : "当前配置缺失或损坏。请选择备份恢复；写入前仍需退出 ChatGPT/Codex。"
    }

    func run(_ command: String, _ payload: [String:Any] = [:]) {
        if isPreview {return}
        guard !busy && !checkingAccess else { return }
        guard let input = try? JSONSerialization.data(withJSONObject:payload) else {error="参数无法编码";return}
        let threadID = payload["id"] as? String
        busy = true
        liveGeneration += 1
        if command != "status" && command != "startup" { notice = "正在处理，请勿关闭本工具。首次备份可能需要一两分钟……" }
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                var access = try JSONDecoder().decode(FileAccessReport.self,from:self.execute("quick-permissions"))
                guard access.ready || ((command == "restore" || command == "recover-database") && access.canRestore) else {
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
                    let data = try self.execute(command,input:input)
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
                if command == "restore" || command == "recover-database" {
                    access = try JSONDecoder().decode(FileAccessReport.self,from:self.execute("quick-permissions"))
                }
                let status = try JSONDecoder().decode(Snapshot.self,from:self.execute(command == "startup" ? "startup" : "status"))
                DispatchQueue.main.async {
                    self.fileAccess = access; self.accessError = nil
                    self.recovery = nil
                    self.snapshot = status
                    self.diagnoses = [:]
                    if command == "switch" {self.operationAttention = []}
                    else if command == "bridge", let id = threadID {
                        self.operationAttention.removeAll {$0.id == id}
                        self.operationAttention.append(contentsOf:attention ?? [])
                    }
                    self.live = status.liveStatus; self.liveError = nil
                    self.keep = status.settings.keep; self.days = status.settings.days
                    self.autoCleanup = status.settings.auto_cleanup
                    if !self.loaded {
                        self.provider = status.provider == "deepseek" ? "deepseek" : "openai"
                        self.model = (self.choices[self.provider] ?? []).contains(status.model) ? status.model : self.choices[self.provider]?.first ?? ""
                        self.effort = status.effort
                        self.normalizeTarget()
                        self.loaded = true
                    }
                    if let message = message {self.notice = message}
                    else if command == "startup" {self.notice = "已读取配置和会话索引；启动不扫描正文。写入前会完整检查并备份。"}
                    self.busy = false
                    if command == "save-key" {self.keySaved = true}
                    if let report = report {self.report = report}
                    if let tid = newThread {self.openThread(tid)}
                }
            } catch {
                let recovery = self.readRecovery()
                let data = (error as NSError).userInfo["fileAccessReport"] as? Data
                let access = data.flatMap {try? JSONDecoder().decode(FileAccessReport.self,from:$0)}
                    ?? (try? JSONDecoder().decode(FileAccessReport.self,from:self.execute("quick-permissions")))
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

