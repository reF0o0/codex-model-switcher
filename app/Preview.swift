import SwiftUI
import AppKit

extension AppModel {
    var switchPreviewText:String {
        guard let preview = switchPreview else {return "请先检查变更预览。"}
        let scope = preview.migrate ? "最近 \(preview.scope_days) 天，候选 \(preview.candidate_threads) 个会话（异常会话可能跳过）" : "历史会话：0 个迁移"
        return "当前：\(preview.current_provider) · \(preview.current_model)\n目标：\(preview.provider) · \(preview.model) · \(preview.effort)\n内置搜索：\(preview.web_search)\n\(scope)\n\(preview.message)"
    }

    func prepareSwitch() {
        if isPreview {return}
        guard canWrite && !preparingSwitch else {return}
        guard let input = try? JSONSerialization.data(withJSONObject:["provider":provider,"model":model,"effort":effort,"days":switchDays,"migrate":!defaultOnly]) else {return}
        preparingSwitch = true
        let client = backend
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                let preview = try JSONDecoder().decode(SwitchPreview.self,from:client.execute("preview-switch",input:input))
                DispatchQueue.main.async {self.switchPreview=preview;self.preparingSwitch=false;self.confirmSwitch=true}
            } catch {
                let message=error.localizedDescription
                DispatchQueue.main.async {self.error=message;self.preparingSwitch=false}
            }
        }
    }

    func performPreparedSwitch() {
        guard let preview = switchPreview else {return}
        switchPreview = nil
        run("switch",["provider":preview.provider,"model":preview.model,"effort":preview.effort,
                      "days":preview.scope_days,"migrate":preview.migrate])
    }

    func loadPreview() {
        do {
            guard let snapshotURL=Bundle.main.url(forResource:"PreviewState",withExtension:"json"),
                  let permissionURL=Bundle.main.url(forResource:"PreviewPermissions",withExtension:"json") else {return}
            snapshot=try JSONDecoder().decode(Snapshot.self,from:Data(contentsOf:snapshotURL))
            fileAccess=try JSONDecoder().decode(FileAccessReport.self,from:Data(contentsOf:permissionURL))
            live=snapshot?.liveStatus
            loaded=true
            notice="开发预览：合成数据，不读写真实配置、钥匙串或会话。"
        } catch {self.error="开发预览数据无法加载："+error.localizedDescription}
    }
}

@MainActor
enum PreviewDriver {
    static var started = false
    static var previewWindow:NSWindow?
    static func launch() {
        let application=NSApplication.shared
        application.setActivationPolicy(.regular)
        let model=AppModel(preview:true)
        let window=NSWindow(contentRect:NSRect(x:0,y:0,width:940,height:700),
                            styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
        window.title="模型切换器 · 开发预览（合成数据）"
        window.contentView=NSHostingView(rootView:ContentView(model:model))
        previewWindow=window
        window.center();window.makeKeyAndOrderFront(nil)
        application.activate(ignoringOtherApps:true)
        start(model)
        application.run()
    }
    static func start(_ app:AppModel) {
        guard !started, let index=CommandLine.arguments.firstIndex(of:"--snapshot-dir"),
              CommandLine.arguments.indices.contains(index+1) else {return}
        started=true
        let directory=URL(fileURLWithPath:CommandLine.arguments[index+1])
        Task { @MainActor in
            do {
                try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
                try await Task.sleep(for:.milliseconds(500))
                guard let window=previewWindow ?? NSApp.windows.first(where:{$0.isVisible}) else {throw CocoaError(.fileNoSuchFile)}
                for appearance in ["light","dark"] {
                    NSApp.appearance=NSAppearance(named:appearance=="light" ? .aqua : .darkAqua)
                    for tab in 0..<4 {
                        app.tab=tab
                        window.makeKeyAndOrderFront(nil)
                        NSApp.activate(ignoringOtherApps:true)
                        try await Task.sleep(for:.milliseconds(400))
                        if !window.isKeyWindow {
                            NSApp.activate(ignoringOtherApps:true)
                            window.makeKeyAndOrderFront(nil)
                            try await Task.sleep(for:.milliseconds(300))
                        }
                        guard window.isKeyWindow else {throw CocoaError(.userCancelled)}
                        let capture=Process()
                        capture.executableURL=URL(fileURLWithPath:"/usr/sbin/screencapture")
                        capture.arguments=["-x","-o","-l",String(window.windowNumber),directory.appendingPathComponent("\(appearance)-\(tab).png").path]
                        try capture.run();capture.waitUntilExit()
                        guard capture.terminationStatus==0 else {throw CocoaError(.fileWriteUnknown)}
                    }
                }
                let info:[String:Any] = ["preview":true,"pages":4,"appearances":2,"real_backend_used":false]
                try JSONSerialization.data(withJSONObject:info).write(to:directory.appendingPathComponent("result.json"))
                NSApp.terminate(nil)
            } catch {
                FileHandle.standardError.write(Data("Snapshot failed: \(error)\n".utf8))
                exit(1)
            }
        }
    }
}
