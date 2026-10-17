import SwiftUI
import AppKit

struct ThreadRow: Identifiable, Decodable {
    let id: String
    let title: String
    let model_provider: String
    let model: String?
    let archived: Int
    let continuation_id: String?
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
struct Snapshot: Decodable {
    let provider: String
    let model: String
    let effort: String
    let threads: [ThreadRow]
    let backups: [BackupRow]
    let settings: Preferences
    let desktop_running: Bool
    let pending: [String:String]?
    let backup_path: String
    let key_configured: Bool
}

final class AppModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var busy = false
    @Published var notice = "先查看当前状态，再选择切换目标。"
    @Published var error: String?
    @Published var provider = "openai"
    @Published var model = "gpt-6.1-sol"
    @Published var effort = "high"
    @Published var keep = 10
    @Published var days = 30
    @Published var autoCleanup = true
    @Published var tab = 0
    @Published var query = ""
    @Published var showArchived = true
    @Published var restoreID: String? = nil
    @Published var confirmSwitch = false
    @Published var confirmRestore = false
    @Published var key = ""
    var loaded = false
    let choices = ["openai":["gpt-6.1-sol","gpt-6-sol","gpt-6-luna","gpt-6-astra"],
                   "deepseek":["deepseek-flash","deepseek-v4-pro"]]

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
            throw NSError(domain:"Switcher",code:3,userInfo:[NSLocalizedDescriptionKey:json["error"] as? String ?? "操作失败；请检查文件权限。"])
        }
        return try JSONSerialization.data(withJSONObject:value)
    }

    func run(_ command: String, _ payload: [String:Any] = [:]) {
        guard !busy else { return }
        busy = true
        if command != "status" && command != "startup" { notice = "正在处理，请勿关闭本工具。首次备份可能需要一两分钟……" }
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                var message: String?
                var newThread: String?
                if command != "status" && command != "startup" {
                    let data = try self.execute(command,payload)
                    let result = try JSONSerialization.jsonObject(with:data) as? [String:Any] ?? [:]
                    newThread = result["thread_id"] as? String
                    if let n = result["migrated"] as? Int { message = "已切换 \(n) 个会话，内容和归档状态保留。现在可以打开 ChatGPT。" }
                    else if let m = result["message"] as? String {message = m}
                    else if command == "backup" {message = "完整备份已创建并完成自动清理。"}
                    else if let n = result["removed"] as? Int {message = "已清理 \(n) 份过期备份。"}
                    else {message = "设置已保存。"}
                }
                let status = try JSONDecoder().decode(Snapshot.self,from:self.execute(command == "startup" ? "startup" : "status"))
                DispatchQueue.main.async {
                    self.snapshot = status
                    self.keep = status.settings.keep; self.days = status.settings.days
                    self.autoCleanup = status.settings.auto_cleanup
                    if !self.loaded {
                        self.provider = status.provider == "deepseek" ? "deepseek" : "openai"
                        self.model = self.choices[self.provider]!.contains(status.model) ? status.model : self.choices[self.provider]![0]
                        self.loaded = true
                    }
                    if let message = message {self.notice = message}
                    else if command == "startup" {self.notice = "已加载全部历史。切换前先退出 ChatGPT，完成后再重新打开。"}
                    self.busy = false
                    if let tid = newThread, let url = URL(string:"codex://threads/\(tid)") {NSWorkspace.shared.open(url)}
                }
            } catch {
                DispatchQueue.main.async {self.error = error.localizedDescription; self.busy = false; self.notice = "操作停止；原会话保留。"}
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var app = AppModel()
    var filtered: [ThreadRow] {
        (app.snapshot?.threads ?? []).filter {
            (app.showArchived || $0.archived == 0) && (app.query.isEmpty || $0.title.localizedCaseInsensitiveContains(app.query) || $0.id.contains(app.query))
        }
    }

    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack(spacing:12) {
                Image(systemName:"arrow.triangle.2.circlepath.circle.fill").font(.system(size:38)).foregroundStyle(.blue)
                VStack(alignment:.leading,spacing:4) {
                    Text("Codex 模型切换器").font(.title2.bold())
                    Text("GPT · DeepSeek · 同一份历史").foregroundStyle(.secondary)
                }
                Spacer()
                Button("刷新",systemImage:"arrow.clockwise") {app.run("status")}
            }
            if let status = app.snapshot {
                HStack(spacing:22) {
                    Label("\(status.model) · \(status.effort)",systemImage:"cpu")
                    Label("\(status.threads.count) 个会话",systemImage:"bubble.left.and.bubble.right")
                    Label("\(status.backups.count) 份备份",systemImage:"externaldrive")
                }.font(.callout)
                if status.pending != nil {
                    Label("检测到未完成的切换，请到备份页恢复。",systemImage:"exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                if status.desktop_running {
                    Label("ChatGPT 正在运行。切换、备份或恢复前，请在 ChatGPT 按 ⌘Q 退出。",systemImage:"info.circle")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            TabView(selection:$app.tab) {
                switchView.tabItem {Label("切换模型",systemImage:"arrow.triangle.swap")}.tag(0)
                historyView.tabItem {Label("全部历史",systemImage:"clock")}.tag(1)
                backupView.tabItem {Label("备份与恢复",systemImage:"externaldrive")}.tag(2)
            }
            Divider()
            HStack {
                if app.busy {ProgressView().controlSize(.small)}
                Text(app.notice).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                Spacer()
                Button("打开 ChatGPT") {NSWorkspace.shared.open(URL(fileURLWithPath:"/Applications/ChatGPT.app"))}
                    .disabled(app.busy)
            }
        }
        .padding(24).frame(minWidth:840,minHeight:660)
        .onAppear {app.run("startup")}
        .onReceive(NotificationCenter.default.publisher(for:NSApplication.didBecomeActiveNotification)) {_ in app.run("status")}
        .alert("操作未完成",isPresented:Binding(get:{app.error != nil},set:{if !$0 {app.error=nil}})) {
            Button("知道了",role:.cancel) {app.error=nil}
        } message: {Text(app.error ?? "")}
        .confirmationDialog("切换全部 GPT／DeepSeek 会话到 \(app.model)？",isPresented:$app.confirmSwitch,titleVisibility:.visible) {
            Button("备份并切换") {app.run("switch",["provider":app.provider,"model":app.model,"effort":app.effort])}
            Button("取消",role:.cancel) {}
        } message: {Text("保留会话 ID、消息、项目和归档状态。先退出 ChatGPT，完成后再重新打开。")}
        .confirmationDialog("恢复选中的模型与服务商设置？",isPresented:$app.confirmRestore,titleVisibility:.visible) {
            Button("创建安全备份并恢复") {if let id=app.restoreID {app.run("restore",["id":id])}}
            Button("取消",role:.cancel) {}
        } message: {Text("恢复点之后的新会话和新消息仍会保留。此功能不会用旧数据库覆盖当前历史。")}
    }

    var switchView: some View {
        VStack(alignment:.leading,spacing:22) {
            Text("选择下一次使用的模型").font(.headline)
            Picker("服务商",selection:$app.provider) {
                Text("GPT · ChatGPT 账号额度").tag("openai")
                Text("DeepSeek · 自己的 API 额度").tag("deepseek")
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
                }.frame(width:170)
            }
            Text(app.provider == "openai" ? "使用现有 ChatGPT 登录。默认推荐 GPT‑6.1 Sol / High；实际可用型号取决于账号。" : "DeepSeek 请求发送到 api.deepseek.com；ChatGPT 登录凭据会保留。")
                .font(.callout).foregroundStyle(.secondary)
            Button("备份并切换全部会话",systemImage:"arrow.triangle.swap") {app.confirmSwitch=true}
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(app.busy || app.snapshot?.desktop_running == true || app.snapshot?.pending != nil)
            Divider()
            DisclosureGroup("DeepSeek 密钥设置（仅需首次或更换密钥）") {
                HStack {
                    SecureField(app.snapshot?.key_configured == true ? "已配置；输入新密钥以替换" : "输入 DeepSeek API key",text:$app.key)
                    Button("保存密钥") {app.run("save-key",["key":app.key]);app.key=""}
                        .disabled(app.key.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || app.busy || app.snapshot?.desktop_running == true)
                }.padding(.top,8)
            }
            Text("消息正文保持原样；只同步文件头的服务商，并保持字节长度不变。若旧消息格式不兼容，保留原会话后可用新会话接续。")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
        }.padding(22).disabled(app.busy)
    }

    var historyView: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack {
                TextField("搜索标题或会话 ID",text:$app.query).textFieldStyle(.roundedBorder)
                Toggle("包含已归档",isOn:$app.showArchived).toggleStyle(.checkbox)
                Text("\(filtered.count) 条").foregroundStyle(.secondary)
            }
            List(filtered) {thread in
                HStack {
                    Image(systemName:thread.archived == 1 ? "archivebox" : "bubble.left")
                        .foregroundStyle(.secondary)
                    VStack(alignment:.leading,spacing:3) {
                        Text(thread.title.isEmpty ? "未命名会话" : thread.title).lineLimit(1)
                        Text("\(thread.model_provider) · \(thread.model ?? "默认模型") · \(thread.id)")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer()
                    Button("原会话") {if let url=URL(string:"codex://threads/\(thread.id)") {NSWorkspace.shared.open(url)}}
                    if let tid = thread.continuation_id {
                        Button("打开接续") {if let url=URL(string:"codex://threads/\(tid)") {NSWorkspace.shared.open(url)}}
                    }
                    Button("兼容接续") {app.run("bridge",["id":thread.id])}
                        .disabled(app.busy || app.snapshot?.desktop_running == true)
                    Button("复制 ID") {NSPasteboard.general.clearContents();NSPasteboard.general.setString(thread.id,forType:.string)}
                }.padding(.vertical,4)
            }.listStyle(.inset)
            Text("旧会话切换后若无法直接回复，先退出 ChatGPT，再点“兼容接续”。它会保留原会话、建立关联文本接续，并使用一次当前模型额度。超长对话加载最近 8 万字符，全文另存；图片和工具记录保留在原会话。")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(20)
    }

    var backupView: some View {
        VStack(alignment:.leading,spacing:16) {
            HStack(spacing:20) {
                Toggle("自动清理",isOn:$app.autoCleanup).toggleStyle(.checkbox)
                Stepper("最近 \(app.keep) 份",value:$app.keep,in:3...100).frame(width:160)
                Stepper("最长 \(app.days) 天",value:$app.days,in:1...3650).frame(width:170)
                Button("保存规则") {app.run("settings",["keep":app.keep,"days":app.days,"auto_cleanup":app.autoCleanup])}
            }
            Text("始终保留最近 3 份成功备份。自动清理在本工具打开时、成功备份后执行；关闭后无后台任务。相同内容只保存一份。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("立即备份",systemImage:"plus.circle") {app.run("backup")}.disabled(app.snapshot?.desktop_running == true)
                Button("立即清理",systemImage:"trash") {app.run("cleanup")}
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
                Text("恢复只回退模型和路由；完整历史快照保留用于人工灾难恢复。")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("恢复选中备份") {app.confirmRestore=true}
                    .disabled(app.restoreID == nil || app.snapshot?.desktop_running == true)
            }
        }.padding(20).disabled(app.busy)
    }
}

@main
struct ProviderSwitcherApp: App {
    var body: some Scene {
        WindowGroup {ContentView()}
            .defaultSize(width:940,height:700)
    }
}
