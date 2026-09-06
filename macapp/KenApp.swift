// Ken menu bar app — a dot in the menu bar that runs the same bot.py the
// installer sets up, so nobody has to open a terminal to know it's alive.
//
// It replaces the launchd agent as the thing that keeps Ken running: on
// launch it boots the agent out (so two copies never poll Telegram at once),
// starts ~/.ken/venv/bin/python ~/.ken/app/bot.py as a child, restarts it if
// it exits, and writes ~/.ken/app.pid so the `ken` CLI knows who's in charge.
// Quitting the app stops Ken. `ken start` brings the agent back.
//
// Because bot.py (and Claude Code under it) run as children of this app,
// macOS attributes their file access to "Ken" — so the Desktop/Documents
// privacy prompt shows up with a real name instead of silently denying a
// background python.

import AppKit
import ServiceManagement
import SwiftUI

// MARK: - Paths

struct KenPaths {
    let home: URL

    init() {
        let env = ProcessInfo.processInfo.environment
        if let h = env["KEN_HOME"], !h.isEmpty {
            home = URL(fileURLWithPath: h)
        } else {
            home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".ken")
        }
    }

    var python: URL { home.appendingPathComponent("venv/bin/python") }
    var bot: URL { home.appendingPathComponent("app/bot.py") }
    var log: URL { home.appendingPathComponent("logs/ken.log") }
    var soul: URL { home.appendingPathComponent("work/SOUL.md") }
    var dotenv: URL { home.appendingPathComponent(".env") }
    var botname: URL { home.appendingPathComponent(".botname") }
    var appPid: URL { home.appendingPathComponent("app.pid") }
    var plist: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/dev.kencomputer.ken.plist")
    }

    var installed: Bool {
        let fm = FileManager.default
        return fm.isExecutableFile(atPath: python.path)
            && fm.fileExists(atPath: bot.path)
            && fm.fileExists(atPath: dotenv.path)
    }

    /// Same PATH the launchd plist gives the bot, plus Homebrew.
    var path: String {
        let h = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(h)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    }
}

// MARK: - Controller

@MainActor
final class Ken: ObservableObject {
    static let shared = Ken()

    enum State { case notInstalled, stopped, running, updating }

    @Published var state: State = .stopped
    @Published var name = "Ken"
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled

    let paths = KenPaths()
    private var process: Process?
    private var wantRunning = true
    private var quitting = false
    private var onExit: (() -> Void)?
    private var restartTimer: Timer?
    private var poll: Timer?
    private var signalSources: [DispatchSourceSignal] = []

    private init() {
        installSignalHandlers()
        poll = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        tick()
    }

    // MARK: status

    var statusLine: String {
        switch state {
        case .notInstalled: return "Ken isn't installed yet"
        case .stopped: return "○ \(name) is stopped"
        case .running: return "● \(name) is running"
        case .updating: return "↻ Updating \(name)…"
        }
    }

    var symbol: String {
        switch state {
        case .notInstalled: return "circle.dashed"
        case .stopped: return "circle"
        case .running: return "circle.fill"
        case .updating: return "circle.dotted"
        }
    }

    /// Runs every few seconds: picks up a fresh install, a rename, or a
    /// launchd agent that came back, and (re)starts the bot when it should be up.
    func tick() {
        let n = (try? String(contentsOf: paths.botname, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        name = n.isEmpty ? "Ken" : n

        guard paths.installed else {
            if process == nil { state = .notInstalled }
            return
        }
        if state == .notInstalled { state = .stopped }
        if process == nil, wantRunning, state != .updating, !quitting {
            start()
        }
    }

    // MARK: process lifecycle

    func start() {
        guard paths.installed, process == nil, !quitting else { return }
        wantRunning = true
        restartTimer?.invalidate()
        takeOverFromLaunchd()

        let p = Process()
        p.executableURL = paths.python
        p.arguments = [paths.bot.path]
        var env = ProcessInfo.processInfo.environment
        env["KEN_HOME"] = paths.home.path
        env["PATH"] = paths.path
        p.environment = env
        p.currentDirectoryURL = paths.home

        try? FileManager.default.createDirectory(
            at: paths.log.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let fh = appendHandle() {
            p.standardOutput = fh
            p.standardError = fh
        }
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor in self?.exited(status: status) }
        }
        do {
            try p.run()
        } catch {
            log("couldn't start bot.py: \(error.localizedDescription)")
            state = .stopped
            return
        }
        process = p
        state = .running
        writePid()
        log("started bot.py (pid \(p.processIdentifier))")
    }

    /// SIGTERM the bot (python-telegram-bot shuts down cleanly on it), SIGKILL
    /// if it hasn't gone in 8s. `then` runs once it has actually exited.
    func stop(then: (() -> Void)? = nil) {
        wantRunning = false
        restartTimer?.invalidate()
        guard let p = process, p.isRunning else {
            process = nil
            state = paths.installed ? .stopped : .notInstalled
            then?()
            return
        }
        onExit = then
        let pid = p.processIdentifier
        p.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 8) {
            if p.isRunning { kill(pid, SIGKILL) }
        }
    }

    func restart() {
        log("restart requested")
        stop { [weak self] in self?.start() }
    }

    private func exited(status: Int32) {
        process = nil
        if state != .updating { state = .stopped }
        if let cb = onExit {
            onExit = nil
            cb()
            return
        }
        guard wantRunning, !quitting else { return }
        // Same contract as launchd KeepAlive: an exit is not a stop. The bot
        // exits on purpose after a self-update, expecting exactly this.
        log("bot.py exited (status \(status)) — restarting in 5s")
        restartTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.start() }
        }
    }

    /// Called from applicationWillTerminate. Blocks briefly so the bot is
    /// really gone before we are — an orphaned bot would keep polling Telegram.
    func shutdown() {
        quitting = true
        wantRunning = false
        restartTimer?.invalidate()
        if let p = process, p.isRunning {
            let pid = p.processIdentifier
            p.terminate()
            for _ in 0..<50 where p.isRunning { usleep(100_000) }
            if p.isRunning { kill(pid, SIGKILL) }
        }
        process = nil
        try? FileManager.default.removeItem(at: paths.appPid)
        log("quit")
    }

    /// The installer registers a launchd agent. While this app is running,
    /// that agent must not: two bots on one token fight over Telegram updates.
    /// `disable` persists so it doesn't come back at next login either;
    /// `ken start` (launchctl load -w) re-enables it if the user goes back.
    private func takeOverFromLaunchd() {
        guard FileManager.default.fileExists(atPath: paths.plist.path) else { return }
        let target = "gui/\(getuid())/dev.kencomputer.ken"
        run("/bin/launchctl", ["bootout", target])
        run("/bin/launchctl", ["disable", target])
    }

    /// O_APPEND, like launchd's StandardOutPath: the bot and this app both
    /// write to ken.log, and without it their file positions overwrite each other.
    private func appendHandle() -> FileHandle? {
        let fd = open(paths.log.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        return fd < 0 ? nil : FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }

    private func writePid() {
        try? String(getpid()).write(to: paths.appPid, atomically: true, encoding: .utf8)
    }

    // MARK: update

    func update() {
        guard paths.installed, state != .updating else { return }
        state = .updating
        let home = paths.home.path
        let script = """
        set -e
        echo "→ updating ken…"
        git -C "\(home)/app" pull --ff-only
        "\(home)/venv/bin/pip" install -q -r "\(home)/app/requirements.txt"
        cp "\(home)/app/ken" "$HOME/.local/bin/ken" 2>/dev/null || true
        echo "✓ updated"
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", script]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = paths.path
        p.environment = env
        if let fh = appendHandle() {
            p.standardOutput = fh
            p.standardError = fh
        }
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor in
                guard let self else { return }
                self.state = self.process == nil ? .stopped : .running
                if status == 0 {
                    self.restart()
                } else {
                    self.log("update failed (status \(status)) — see logs")
                    self.alert("Update failed", "Check View Logs for what went wrong.")
                }
            }
        }
        do { try p.run() } catch {
            state = process == nil ? .stopped : .running
            alert("Update failed", error.localizedDescription)
        }
    }

    // MARK: menu actions

    func viewLogs() {
        NSWorkspace.shared.open(paths.log)
    }

    func editMemory() {
        openInEditor(paths.soul)
    }

    func editSettings() {
        openInEditor(paths.dotenv)
    }

    func openWebsite() {
        NSWorkspace.shared.open(URL(string: "https://kencomputer.dev")!)
    }

    /// Opens Terminal running the one-line installer. Written to a temp file
    /// at runtime so it never carries a quarantine flag from the download.
    func install() {
        let script = """
        #!/bin/bash
        clear
        echo "Installing Ken — follow the prompts. Close this window when it says it's done."
        echo
        curl -fsSL https://kencomputer.dev/install | bash
        echo
        echo "You can close this window now — Ken is in your menu bar."
        """
        let url = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("install-ken.command")
        do {
            try script.write(to: url, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
            run("/usr/bin/open", ["-a", "Terminal", url.path])
        } catch {
            alert("Couldn't open the installer", error.localizedDescription)
        }
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            alert("Couldn't change login item",
                  "\(error.localizedDescription)\n\nMove Ken to your Applications folder and try again.")
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    // MARK: helpers

    private func openInEditor(_ url: URL) {
        // `open -t`: whatever the user's default plain-text editor is.
        run("/usr/bin/open", ["-t", url.path])
    }

    @discardableResult
    private func run(_ exe: String, _ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return -1 }
        p.waitUntilExit()
        return p.terminationStatus
    }

    private func log(_ line: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        let text = "\(stamp) INFO [menubar] \(line)\n"
        guard let data = text.data(using: .utf8) else { return }
        appendHandle()?.write(data)
        NSLog("%@", line)
    }

    private func alert(_ title: String, _ message: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = message
        a.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }

    /// SIGTERM/SIGINT/SIGHUP: quit properly (so the bot is stopped too).
    /// SIGUSR1: restart the bot — this is what `ken restart` sends us.
    private func installSignalHandlers() {
        for (sig, action) in [
            (SIGTERM, { NSApp.terminate(nil) }),
            (SIGINT, { NSApp.terminate(nil) }),
            (SIGHUP, { NSApp.terminate(nil) }),
            (SIGUSR1, { Ken.shared.restart() }),
        ] as [(Int32, @MainActor () -> Void)] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { Task { @MainActor in action() } }
            src.resume()
            signalSources.append(src)
        }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillTerminate(_ notification: Notification) {
        Ken.shared.shutdown()
    }
}

@main
struct KenMenuBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var ken = Ken.shared

    var body: some Scene {
        MenuBarExtra {
            MenuContent(ken: ken)
        } label: {
            Image(systemName: ken.symbol)
        }
    }
}

struct MenuContent: View {
    @ObservedObject var ken: Ken

    var body: some View {
        Text(ken.statusLine)
        Divider()
        switch ken.state {
        case .notInstalled:
            Button("Install Ken…") { ken.install() }
            Button("What is Ken?") { ken.openWebsite() }
        case .stopped:
            Button("Start \(ken.name)") { ken.start() }
            Button("Update Ken") { ken.update() }
            Divider()
            tools
        case .running, .updating:
            Button("Restart \(ken.name)") { ken.restart() }
            Button("Stop \(ken.name)") { ken.stop() }
            Button("Update Ken") { ken.update() }.disabled(ken.state == .updating)
            Divider()
            tools
        }
        Divider()
        Toggle("Start at Login", isOn: Binding(
            get: { ken.launchAtLogin },
            set: { ken.setLaunchAtLogin($0) }
        ))
        Button("Quit (stops \(ken.name))") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    @ViewBuilder
    private var tools: some View {
        Button("View Logs") { ken.viewLogs() }
        Button("Edit Memory (SOUL.md)") { ken.editMemory() }
        Button("Edit Settings (.env)") { ken.editSettings() }
    }
}
