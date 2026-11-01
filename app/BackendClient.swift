import Foundation
import os

final class BackendClient: Sendable {
    private let pythonCache = OSAllocatedUnfairLock<String?>(initialState:nil)
    func execute(_ command: String, input: Data, timeout: TimeInterval? = nil) throws -> Data {
        guard let python = supportedPython(),
              let resource = Bundle.main.resourceURL else {
            throw NSError(domain:"Switcher",code:1,userInfo:[NSLocalizedDescriptionKey:
                "找不到 Python 3.11+。已检查常见安装位置和 PATH；请安装 Python 3.11+ 或 Homebrew python3，然后重新打开本工具。"])
        }
        let run: ProcessRunResult
        do {
            run = try runProcess(executable:URL(fileURLWithPath:python),
                                 arguments:["-B", "-s", "-E",
                                            resource.appendingPathComponent("backend/cli.py").path, command],
                                 input:input,
                                 currentDirectory:FileManager.default.homeDirectoryForCurrentUser,
                                 timeout:timeout ?? defaultTimeout(command))
        } catch {
            throw NSError(domain:"Switcher",code:4,userInfo:[NSLocalizedDescriptionKey:error.localizedDescription])
        }
        guard let json = try JSONSerialization.jsonObject(with:run.stdout) as? [String:Any] else {
            throw NSError(domain:"Switcher",code:2,userInfo:[NSLocalizedDescriptionKey:
                "后端未返回有效结果，请检查 Python 版本。" + redactedBackendError(run.stderr)])
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

    private func supportedPython() -> String? {
        if let pythonExecutable = pythonCache.withLock({$0}), FileManager.default.isExecutableFile(atPath:pythonExecutable) {
            return pythonExecutable
        }
        var candidates = ["/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
                          "/opt/homebrew/bin/python3.14", "/opt/homebrew/bin/python3.13",
                          "/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3.11",
                          "/opt/homebrew/bin/python3", "/usr/local/bin/python3.14",
                          "/usr/local/bin/python3.13", "/usr/local/bin/python3.12",
                          "/usr/local/bin/python3.11", "/usr/local/bin/python3",
                          "/usr/bin/python3"]
        if let path = ProcessInfo.processInfo.environment["PATH"] {
            for folder in path.split(separator:":") {
                candidates.append(String(folder)+"/python3")
            }
        }
        var seen=Set<String>()
        for candidate in candidates where seen.insert(candidate).inserted {
            guard FileManager.default.isExecutableFile(atPath:candidate) else {continue}
            let probe = try? runProcess(executable:URL(fileURLWithPath:candidate),
                                        arguments:["-c","import sys, tomllib; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"],
                                        input:Data(),timeout:5)
            if probe?.status == 0 {
                pythonCache.withLock {$0 = candidate}
                return candidate
            }
        }
        return nil
    }

    private func defaultTimeout(_ command:String) -> TimeInterval {
        switch command {
        case "status","startup","permissions","live-status","recovery-status","diagnose":return 90
        case "backup","switch","restore","recover-database","repair","repair-formats","repair-deepseek","web-search":return 900
        case "bridge":return 600
        default:return 300
        }
    }

}
