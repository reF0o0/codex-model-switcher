import Foundation

@main
struct ProcessTimeoutChecks {
    static func main() throws {
        let echo = try runProcess(executable:URL(fileURLWithPath:"/bin/cat"),
                                  arguments:[],input:Data("hello".utf8),timeout:2)
        guard echo.stdout == Data("hello".utf8) else {
            fatalError("stdout was not captured")
        }
        let stderr = try runProcess(executable:URL(fileURLWithPath:"/bin/sh"),
                                    arguments:["-c","echo boom >&2"],input:Data(),timeout:2)
        guard stderr.stderr.contains("boom") else {
            fatalError("stderr was not captured")
        }
        let started = Date()
        do {
            _ = try runProcess(executable:URL(fileURLWithPath:"/bin/sleep"),
                               arguments:["5"],input:Data(),timeout:0.2)
            fatalError("timeout was not enforced")
        } catch {
            guard Date().timeIntervalSince(started) < 3 else {
                fatalError("timeout took too long")
            }
        }
        print("Process timeout checks: PASS")
    }
}
