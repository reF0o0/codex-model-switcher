import SwiftUI
import AppKit
import os

struct ProcessRunResult: Sendable {
    let stdout: Data
    let stderr: String
    let status: Int32
}

enum ProcessRunError: LocalizedError {
    case timeout(TimeInterval,String)
    case input(String)

    var errorDescription: String? {
        switch self {
        case .timeout(let seconds,let detail):
            let tail = detail.isEmpty ? "" : " 后端输出：" + detail
            return "后端超过 \(Int(seconds)) 秒未返回，已终止本次操作。若这是写入操作，请检查备份页的未完成提示。" + tail
        case .input(let detail):
            return "无法把请求交给后端。\(detail)"
        }
    }
}

func runProcess(executable:URL,arguments:[String],input:Data,
                currentDirectory:URL?=nil,timeout:TimeInterval) throws -> ProcessRunResult {
    let task = Process()
    task.executableURL = executable
    task.arguments = arguments
    if let currentDirectory {task.currentDirectoryURL = currentDirectory}
    let output = Pipe(), stdin = Pipe()
    task.standardOutput = output
    task.standardInput = stdin
    let errorURL = FileManager.default.temporaryDirectory
        .appendingPathComponent("switcher-stderr-\(UUID().uuidString)")
    FileManager.default.createFile(atPath:errorURL.path,contents:nil)
    let errorHandle: FileHandle
    do { errorHandle = try FileHandle(forWritingTo:errorURL) }
    catch { throw ProcessRunError.input("无法创建错误输出文件。") }
    defer {
        try? errorHandle.close()
        try? FileManager.default.removeItem(at:errorURL)
    }
    task.standardError = errorHandle
    defer {if task.isRunning {task.terminate()}}
    try task.run()
    do {
        try stdin.fileHandleForWriting.write(contentsOf:input)
        try stdin.fileHandleForWriting.close()
    } catch {
        let detail = (try? String(contentsOf:errorURL,encoding:.utf8)) ?? ""
        throw ProcessRunError.input(detail.isEmpty ? error.localizedDescription : detail)
    }

    let readSemaphore = DispatchSemaphore(value:0)
    let outputBox = OSAllocatedUnfairLock(initialState:Data())
    DispatchQueue.global(qos:.utility).async {
        let bytes = output.fileHandleForReading.readDataToEndOfFile()
        outputBox.withLock {$0 = bytes}
        readSemaphore.signal()
    }
    if readSemaphore.wait(timeout:.now()+timeout) == .timedOut {
        task.interrupt()
        if readSemaphore.wait(timeout:.now()+2) == .timedOut {task.terminate()}
        _ = readSemaphore.wait(timeout:.now()+2)
        let detail = (try? String(contentsOf:errorURL,encoding:.utf8)) ?? ""
        throw ProcessRunError.timeout(timeout,detail)
    }
    let exitSemaphore = DispatchSemaphore(value:0)
    task.terminationHandler = {_ in exitSemaphore.signal()}
    if task.isRunning && exitSemaphore.wait(timeout:.now()+max(1,timeout)) == .timedOut {
        task.terminate()
        let detail = (try? String(contentsOf:errorURL,encoding:.utf8)) ?? ""
        throw ProcessRunError.timeout(timeout,detail)
    }
    let stderr = (try? String(contentsOf:errorURL,encoding:.utf8)) ?? ""
    return ProcessRunResult(stdout:outputBox.withLock {$0},stderr:stderr,status:task.terminationStatus)
}

func redactedBackendError(_ text:String) -> String {
    var value = text
    for pattern in ["sk-[A-Za-z0-9._\\-]{6,}","gho_[A-Za-z0-9]{20,}"] {
        value = value.replacingOccurrences(of:pattern,with:"<redacted>",options:.regularExpression)
    }
    let trimmed = value.trimmingCharacters(in:.whitespacesAndNewlines)
    return trimmed.isEmpty ? "" : " 后端输出：" + String(trimmed.suffix(1200))
}

