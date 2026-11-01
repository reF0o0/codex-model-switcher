import SwiftUI
import AppKit

struct TermsView: View {
    let onAccept: () -> Void
    let onDecline: () -> Void

    private var termsText: String {
        if let url = Bundle.main.url(forResource:"TERMS",withExtension:"md"),
           let text = try? String(contentsOf:url,encoding:.utf8) {
            return text
        }
        return "本工具会读取并可能修改本机 ~/.codex 下的配置、会话和数据库。\n\n" +
            "请先保存工作并完全退出 ChatGPT/Codex。工具会尽力备份，但不保证恢复所有数据。\n\n" +
            "GPT、ChatGPT、Codex 和 DeepSeek 的使用受各自服务条款、额度和隐私政策约束。\n\n" +
            "本软件按现状提供，不承诺无错误；使用风险由你自行承担。完整条款见仓库 TERMS.md。"
    }

    var body: some View {
        VStack(alignment:.leading,spacing:14) {
            HStack(spacing:8) {
                Image(systemName:"doc.text")
                Text("使用条款").font(.title2.bold())
            }
            ScrollView {
                Text(termsText)
                    .font(.callout)
                    .textSelection(.enabled)
                    .frame(maxWidth:.infinity,alignment:.leading)
            }
            .frame(width:600,height:360)
            Text("点击“同意并继续”表示你已阅读并同意上述条款。不同意请退出应用。")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("退出") {onDecline()}
                Spacer()
                Button("同意并继续") {onAccept()}
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width:660)
        .background(.regularMaterial,in:RoundedRectangle(cornerRadius:14))
        .shadow(radius:24)
        .interactiveDismissDisabled()
    }
}

struct ContentView: View {
    @StateObject private var app: AppModel
    init(model:AppModel = AppModel()) {_app = StateObject(wrappedValue:model)}
    @AppStorage("terms-accepted-v1") private var termsAccepted = false
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
                    Label("检测到未完成或损坏的操作标记，请到备份页选择备份恢复；损坏标记会单独保留。",systemImage:"exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                if app.desktopCheck == "running" {
                    Label("ChatGPT/Codex 正在运行。写入前请按 ⌘Q 退出并关闭 Codex 后台任务，再点刷新状态。",systemImage:"info.circle")
                        .font(.callout).foregroundStyle(.secondary)
                } else if app.desktopCheck == "unknown" {
                    Label("无法确认 ChatGPT 是否在运行（系统限制了进程查询）。切换前请自行确认已按 ⌘Q 退出。",systemImage:"exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                }
                if let error = app.liveError {Text(error).font(.caption).foregroundStyle(.orange)}
                if let warnings = app.snapshot?.warnings, !warnings.isEmpty {
                    DisclosureGroup("工具状态提示 \(warnings.count)") {
                        VStack(alignment:.leading,spacing:4) {
                            ForEach(warnings,id:\.self) {warning in
                                Text(warning).font(.caption).foregroundStyle(.secondary)
                            }
                        }.frame(maxWidth:.infinity,alignment:.leading)
                    }.font(.caption)
                }
            }
            if let recovery = app.recovery {
                GroupBox("应急恢复") {
                    VStack(alignment:.leading,spacing:10) {
                        if recovery.databaseBad {
                            Text("Codex 数据库无法通过完整性检查。先替换损坏或缺失的数据库，再处理配置。")
                                .foregroundStyle(.orange)
                            Text("只会替换损坏的数据库；原文件保留在工具数据目录，备份之后新建的会话可能需要重新扫描。写入前仍需退出 ChatGPT/Codex。")
                                .font(.caption).foregroundStyle(.secondary)
                            if let backups = recovery.database_backups, !backups.isEmpty {
                                HStack {
                                    Picker("数据库备份",selection:$app.databaseRestoreID) {
                                        ForEach(backups) {backup in
                                            Text(backup.created_at + " · " + backup.id).tag(Optional(backup.id))
                                        }
                                    }
                                    Button("从备份修复数据库") {app.confirmDatabaseRestore = true}
                                        .disabled(!app.canRecoverDatabase || app.databaseRestoreID == nil)
                                }
                            } else {
                                Text("未找到含有效数据库的备份；请保留现有数据并从原备份手动恢复。")
                                    .font(.caption).foregroundStyle(.orange)
                            }
                        }
                        if recovery.configBad {
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
                                    Button("从备份恢复配置") {app.confirmRestore = true}
                                        .disabled(!app.canRecover || app.restoreID == nil)
                                }
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
                Text(app.snapshot?.key_configured == true ? "配置中已有密钥。只有更换时才需要输入新的。" : "启动未读取钥匙串；保存或切换时会检查密钥。")
                    .foregroundStyle(.secondary)
                SecureField("粘贴 DeepSeek 密钥",text:$app.key)
                    .textFieldStyle(.roundedBorder)
                Text("需要先完全退出 ChatGPT/Codex。保存时会写入钥匙串和当前配置。")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Spacer()
                    Button("关闭") {app.showKeySheet=false}.keyboardShortcut(.cancelAction)
                    Button("保存") {app.run("save-key",["key":app.key])}
                        .disabled(app.key.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || !app.canWrite)
                        .keyboardShortcut(.defaultAction)
                }
            }.padding(24).frame(width:460)
            .onChange(of:app.keySaved) {_,saved in
                if saved {app.key="";app.showKeySheet=false;app.keySaved=false}
            }
        }
        .padding(24).frame(minWidth:840,minHeight:660)
        .overlay {
            if !termsAccepted && !app.isPreview {
                ZStack {
                    Color.black.opacity(0.35).ignoresSafeArea()
                    TermsView(onAccept:{termsAccepted=true},
                              onDecline:{NSApplication.shared.terminate(nil)})
                }
            }
        }
        .onAppear {if app.isPreview {PreviewDriver.start(app)} else if termsAccepted {app.run("startup")}}
        .onChange(of:termsAccepted) {_,accepted in
            if accepted && !app.loaded {app.run("startup")}
        }
        .alert("操作未完成",isPresented:Binding(get:{app.error != nil},set:{if !$0 {app.error=nil;app.errorNeedsPermission=false}})) {
            if app.errorNeedsPermission {
                Button("申请目录访问…") {app.error=nil; app.errorNeedsPermission=false; app.requestFolderAccess()}
                Button("系统权限设置") {app.error=nil; app.errorNeedsPermission=false; app.openPermissionSettings()}
            }
            Button("知道了",role:.cancel) {app.error=nil; app.errorNeedsPermission=false}
        } message: {Text(app.error ?? "")}
        .confirmationDialog("确认模型变更",isPresented:$app.confirmSwitch,titleVisibility:.visible) {
            Button(app.switchPreview?.migrate == false ? "备份并修改默认模型" : "备份并切换历史") {app.performPreparedSwitch()}
            Button("取消",role:.cancel) {app.switchPreview=nil}
        } message: {Text(app.switchPreviewText)}
        .confirmationDialog("恢复选中的模型、路由与联网设置？",isPresented:$app.confirmRestore,titleVisibility:.visible) {
            Button("创建安全备份并恢复") {if let id=app.restoreID {app.run("restore",["id":id])}}
            Button("取消",role:.cancel) {}
        } message: {Text(app.recovery == nil ? "恢复点之后的新会话和新消息仍会保留。此功能不会用旧数据库覆盖当前历史。" : "当前配置缺失或损坏，将从备份重建配置并恢复模型与路由。损坏文件会单独保留，未备份的设置需手动合并，缺失密钥需重新填写。新增会话和消息仍保留。")}
        .confirmationDialog("从备份修复数据库？",isPresented:$app.confirmDatabaseRestore,titleVisibility:.visible) {
            Button("替换损坏或缺失的数据库") {if let id=app.databaseRestoreID {app.run("recover-database",["id":id])}}
            Button("取消",role:.cancel) {}
        } message: {Text("只替换无法通过完整性检查的数据库；当前文件会单独保留。备份之后新建的会话可能需要重新扫描，配置和其他数据库不在此步骤中改动。")}
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
        .confirmationDialog(app.enableSearch ? "开启 \(app.providerName) 内置网页搜索？" : "关闭 \(app.providerName) 内置网页搜索？",isPresented:$app.confirmSearch,titleVisibility:.visible) {
            Button(app.enableSearch ? "备份并开启" : "备份并关闭") {app.run("web-search",["enabled":app.enableSearch])}
            Button("取消",role:.cancel) {}
        } message: {Text("只控制当前服务商的内置网页搜索，GPT 与 DeepSeek 分别记住选择。DeepSeek 是否可用还取决于模型及服务端支持。搜索与模型调用可能产生费用。MCP、浏览器等外部联网工具不受影响。先退出 ChatGPT/Codex，保存后重新打开。")}
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
                        Button("刷新状态",systemImage:"arrow.clockwise") {app.refreshLiveStatus()}
                            .disabled(app.busy || app.refreshInFlight || !app.loaded)
                    }.padding(4)
                }

                if let checked = app.live?.checked_at {
                    Text("配置读取于 \(Date(timeIntervalSince1970:checked).formatted(date:.abbreviated,time:.standard))；外部更改后请刷新状态。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("切换目标").font(.headline)
                Picker("服务商",selection:$app.provider) {
                    Text("GPT · ChatGPT 额度").tag("openai")
                    Text("DeepSeek · API 额度").tag("deepseek")
                }.pickerStyle(.segmented)
                .onChange(of:app.provider) {_,new in app.model=app.choices[new]![0];app.effort="high";app.normalizeTarget()}
                HStack(spacing:20) {
                    Picker("模型",selection:$app.model) {
                        ForEach(app.choices[app.provider] ?? [],id:\.self) {Text($0).tag($0)}
                    }
                    .onChange(of:app.model) {_,_ in app.normalizeTarget()}
                    Picker("推理",selection:$app.effort) {
                        ForEach(app.effortChoices,id:\.self) {Text($0.capitalized).tag($0)}
                    }.frame(width:160)
                }
                if !app.defaultOnly {
                HStack(spacing:12) {
                        Picker("切换范围",selection:$app.switchDays) {
                        Text("最近 30 天").tag(30)
                        Text("最近 90 天").tag(90)
                        Text("最近 365 天").tag(365)
                    }.pickerStyle(.segmented).frame(width:360)
                }
                Text("更早的会话不会随切换迁移；需要对齐全部旧会话时，使用下方“维护工具 → 只修复路由”。")
                    .font(.caption).foregroundStyle(.secondary)

                }

                if ["openai","deepseek"].contains(app.live?.provider ?? "") {
                    HStack {
                        Label("\(app.providerName) 内置网页搜索：\(app.live?.web_search.description ?? "正在读取搜索状态……")",systemImage:"network")
                            .foregroundStyle(app.live?.web_search.state == "on" ? .green : .secondary)
                        Spacer()
                        Button(app.enableSearch ? "开启搜索" : "关闭搜索") {app.confirmSearch=true}
                            .disabled(!app.canWrite || (app.enableSearch ? app.live?.web_search.can_enable != true : app.live?.web_search.can_disable != true))
                    }
                    Text("GPT 与 DeepSeek V4 Pro 默认开启，分别记住选择；Flash 暂不支持。当前开关对应 \(app.providerName)。")
                        .font(.caption).foregroundStyle(.secondary)
                }

                Toggle("只修改默认模型，保留历史会话路由",isOn:$app.defaultOnly)
                    .toggleStyle(.checkbox)
                Button(app.switchTitle,systemImage:"arrow.triangle.swap") {app.prepareSwitch()}
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .disabled(!app.canWrite || app.preparingSwitch)

                if app.defaultOnly {
                    Text("仅影响默认配置；旧会话保留原路由，接续时可能沿用原模型。")
                        .font(.caption).foregroundStyle(.secondary)
                } else if app.provider == "openai" {
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

                if !app.defaultOnly && app.snapshot?.deepseek_restore == nil {
                    Text("历史格式与还原数量将在切换时检查，启动时不扫描。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing:8) {
                    Button("DeepSeek 密钥设置",systemImage:"key") {app.showKeySheet=true}
                    if app.snapshot?.key_configured == true {
                        Label("已保存",systemImage:"checkmark.seal")
                            .font(.caption).foregroundStyle(.green)
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
                    }
                    .frame(maxWidth:.infinity,alignment:.leading)
                    .padding(.top,8)
                    .padding(.leading,18)
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

    func backupDate(_ value:String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime,.withFractionalSeconds]
        let date = formatter.date(from:value) ?? ISO8601DateFormatter().date(from:value)
        return date?.formatted(date:.abbreviated,time:.standard) ?? value
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
            Text("始终保留最近 3 份成功备份。自动清理在完整备份成功后执行；启动与仅修改默认模型时不扫描清理；关闭后无后台任务。相同内容只保存一份。")
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
                        Text(backupDate(b.created_at)).font(.callout).help(b.created_at)
                        Text("\(b.scope == "configuration" ? "配置备份" : "完整备份") · \(b.files) 个文件 · \(b.id)").font(.caption).foregroundStyle(.secondary)
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

