// NoteSyncBar: menu bar host for the notesync engine (DESIGN.md M5).
// TCC "responsible app": the python watch loop runs as our child and
// inherits the Documents / iCloud grants. v1.1 adds a settings window,
// a note browser with git history, and a reverse-chronological log view;
// v1.2 a format check window (notesync lint), and the browser preview is
// rendered by the engine itself (notesync preview) -- one markdown
// implementation, not two.
import AppKit
import ServiceManagement
import WebKit

let stateDir = NSString(string: "~/Documents/NoteSync").expandingTildeInPath
let syncDir = NSString(string:
    "~/Library/Mobile Documents/com~apple~CloudDocs/SYNC").expandingTildeInPath
let configPath = stateDir + "/config.json"
let logPath = stateDir + "/notesync.log"
let projectDir: String = {
    // engine checkout location; configurable via `engine_dir` in config.json
    if let data = FileManager.default.contents(atPath: configPath),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let d = obj["engine_dir"] as? String {
        return NSString(string: d).expandingTildeInPath
    }
    return NSString(string: "~/notesync").expandingTildeInPath
}()

// MARK: - helpers

@discardableResult
func runGit(_ args: [String]) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
    p.arguments = ["--git-dir", stateDir + "/repo.git", "--work-tree", syncDir] + args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = Pipe()
    do { try p.run() } catch { return "" }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return String(data: data, encoding: .utf8) ?? ""
}

func stripFrontMatter(_ text: String) -> String {
    let lines = text.components(separatedBy: "\n")
    if lines.count >= 3, lines[0] == "---", lines[1].hasPrefix("ns: "), lines[2] == "---" {
        return lines.dropFirst(3).joined(separator: "\n")
    }
    return text
}

func loadConfig() -> [String: Any] {
    guard let data = FileManager.default.contents(atPath: configPath),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return [:] }
    return obj
}

func saveConfig(_ cfg: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: cfg,
            options: [.prettyPrinted, .sortedKeys]) {
        try? data.write(to: URL(fileURLWithPath: configPath))
    }
}

func makeWindow(_ title: String, _ w: CGFloat, _ h: CGFloat) -> NSWindow {
    let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: w, height: h),
                       styleMask: [.titled, .closable, .resizable],
                       backing: .buffered, defer: false)
    win.title = title
    win.isReleasedWhenClosed = false
    win.center()
    return win
}

func present(_ win: NSWindow) {
    NSApp.activate(ignoringOtherApps: true)
    win.makeKeyAndOrderFront(nil)
}

/// Run `python3 -m notesync <args>` in the engine checkout, feeding `input`
/// on stdin. -> (stdout, exit status); status -1 if it could not start.
func runEngine(_ args: [String], input: String? = nil) -> (out: String, status: Int32) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = ["-m", "notesync"] + args
    p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
    var env = ProcessInfo.processInfo.environment
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    p.environment = env
    let outPipe = Pipe(), inPipe = Pipe()
    p.standardOutput = outPipe
    p.standardError = FileHandle.nullDevice
    p.standardInput = inPipe
    do { try p.run() } catch { return ("", -1) }
    if let input = input { inPipe.fileHandleForWriting.write(Data(input.utf8)) }
    try? inPipe.fileHandleForWriting.close()
    let data = outPipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (String(data: data, encoding: .utf8) ?? "", p.terminationStatus)
}

func escapeHTML(_ x: String) -> String {
    x.replacingOccurrences(of: "&", with: "&amp;")
     .replacingOccurrences(of: "<", with: "&lt;")
     .replacingOccurrences(of: ">", with: "&gt;")
}

// MARK: - settings window

final class SettingsController: NSObject {
    let window = makeWindow("NoteSync 设置", 520, 300)
    let appleField = NSTextField(frame: .zero)
    let enField = NSTextField(frame: .zero)
    let gdriveField = NSTextField(frame: .zero)
    let appleCheck = NSButton(checkboxWithTitle: "同步 Apple Notes", target: nil, action: nil)
    let enCheck = NSButton(checkboxWithTitle: "同步 Evernote", target: nil, action: nil)
    let gdriveCheck = NSButton(checkboxWithTitle: "镜像到 Google Drive（单向只读）", target: nil, action: nil)
    var onSave: (() -> Void)?

    override init() {
        super.init()
        let v = window.contentView!
        func label(_ s: String, _ y: CGFloat) {
            let l = NSTextField(labelWithString: s)
            l.frame = NSRect(x: 20, y: y, width: 150, height: 20)
            v.addSubview(l)
        }
        func field(_ f: NSTextField, _ y: CGFloat) {
            f.frame = NSRect(x: 175, y: y - 2, width: 325, height: 24)
            f.autoresizingMask = [.width]
            v.addSubview(f)
        }
        func check(_ c: NSButton, _ y: CGFloat) {
            c.frame = NSRect(x: 175, y: y, width: 325, height: 20)
            v.addSubview(c)
        }
        let storeLabel = NSTextField(labelWithString: "文件端：iCloud Drive/SYNC（固定，即本目录）")
        storeLabel.frame = NSRect(x: 20, y: 258, width: 480, height: 20)
        storeLabel.textColor = .secondaryLabelColor
        v.addSubview(storeLabel)

        label("Apple 文件夹名", 220); field(appleField, 220); check(appleCheck, 194)
        label("Evernote 笔记本名", 160); field(enField, 160); check(enCheck, 134)
        label("Google 镜像路径", 100); field(gdriveField, 100); check(gdriveCheck, 74)

        let save = NSButton(title: "保存并重启引擎", target: self, action: #selector(save(_:)))
        save.frame = NSRect(x: 340, y: 18, width: 160, height: 30)
        save.bezelStyle = .rounded
        save.keyEquivalent = "\r"
        v.addSubview(save)
    }

    func show() {
        let cfg = loadConfig()
        let maps = cfg["mappings"] as? [[String: Any]] ?? []
        let m = maps.first ?? [:]
        appleField.stringValue = m["apple_folder"] as? String ?? "SYNC"
        enField.stringValue = m["en_notebook"] as? String ?? "SYNC"
        gdriveField.stringValue = cfg["gdrive_mirror"] as? String ?? ""
        appleCheck.state = (m["apple_enabled"] as? Bool ?? true) ? .on : .off
        enCheck.state = (m["en_enabled"] as? Bool ?? true) ? .on : .off
        gdriveCheck.state = (cfg["gdrive_enabled"] as? Bool ?? true) ? .on : .off
        present(window)
    }

    @objc func save(_ sender: Any?) {
        var cfg = loadConfig()
        var maps = cfg["mappings"] as? [[String: Any]] ?? [["name": "sync", "folder": ""]]
        var m = maps[0]
        m["apple_folder"] = appleField.stringValue
        m["en_notebook"] = enField.stringValue
        m["apple_enabled"] = appleCheck.state == .on
        m["en_enabled"] = enCheck.state == .on
        maps[0] = m
        cfg["mappings"] = maps
        cfg["gdrive_mirror"] = gdriveField.stringValue.isEmpty ? nil : gdriveField.stringValue
        cfg["gdrive_enabled"] = gdriveCheck.state == .on
        saveConfig(cfg)
        onSave?()
        window.close()
    }
}

// MARK: - note browser with git history

final class BrowserController: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    let window = makeWindow("NoteSync 笔记浏览器", 780, 520)
    let table = NSTableView()
    let versions = NSPopUpButton(frame: .zero, pullsDown: false)
    let sortSel = NSPopUpButton(frame: .zero, pullsDown: false)
    let mode = NSSegmentedControl(labels: ["预览", "源码"], trackingMode: .selectOne,
                                  target: nil, action: nil)
    let text = NSTextView()
    let web = WKWebView(frame: .zero)
    var textScroll: NSScrollView!
    var currentContent = ""
    var fileInfos: [(rel: String, created: Date, modified: Date)] = []
    var commits: [(sha: String, label: String)] = []

    override init() {
        super.init()
        let v = window.contentView!
        sortSel.frame = NSRect(x: 6, y: 488, width: 248, height: 24)
        sortSel.autoresizingMask = [.minYMargin]
        sortSel.addItems(withTitles: ["按更新时间 ↓", "按创建时间 ↓"])
        sortSel.target = self
        sortSel.action = #selector(sortChanged)
        v.addSubview(sortSel)

        let side = NSScrollView(frame: NSRect(x: 0, y: 0, width: 260, height: 482))
        side.autoresizingMask = [.height]
        side.hasVerticalScroller = true
        let colName = NSTableColumn(identifier: .init("f"))
        colName.title = "笔记"
        colName.width = 156
        table.addTableColumn(colName)
        let colDate = NSTableColumn(identifier: .init("d"))
        colDate.title = "日期"
        colDate.width = 78
        table.addTableColumn(colDate)
        table.dataSource = self
        table.delegate = self
        table.headerView = nil
        side.documentView = table
        v.addSubview(side)

        versions.frame = NSRect(x: 268, y: 486, width: 364, height: 26)
        versions.autoresizingMask = [.width, .minYMargin]
        versions.target = self
        versions.action = #selector(versionChanged)
        v.addSubview(versions)

        mode.frame = NSRect(x: 640, y: 486, width: 132, height: 26)
        mode.autoresizingMask = [.minXMargin, .minYMargin]
        mode.selectedSegment = 0
        mode.target = self
        mode.action = #selector(modeChanged)
        v.addSubview(mode)

        let sv = NSScrollView(frame: NSRect(x: 268, y: 8, width: 504, height: 470))
        sv.autoresizingMask = [.width, .height]
        sv.hasVerticalScroller = true
        text.frame = NSRect(origin: .zero, size: sv.contentSize)
        text.isEditable = false
        text.font = NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular)
        text.autoresizingMask = [.width]
        text.textContainerInset = NSSize(width: 8, height: 8)
        sv.documentView = text
        v.addSubview(sv)
        textScroll = sv

        web.frame = sv.frame
        web.autoresizingMask = [.width, .height]
        v.addSubview(web)
    }

    func show() {
        reloadFiles()
        present(window)
    }

    @objc func sortChanged() { reloadFiles() }

    func reloadFiles() {
        var out: [(rel: String, created: Date, modified: Date)] = []
        let base = URL(fileURLWithPath: syncDir)
        let keys: [URLResourceKey] = [.creationDateKey, .contentModificationDateKey]
        if let en = FileManager.default.enumerator(at: base, includingPropertiesForKeys: keys) {
            for case let u as URL in en where u.pathExtension == "md" {
                let rv = try? u.resourceValues(forKeys: Set(keys))
                out.append((String(u.path.dropFirst(syncDir.count + 1)),
                            rv?.creationDate ?? .distantPast,
                            rv?.contentModificationDate ?? .distantPast))
            }
        }
        let byCreated = sortSel.indexOfSelectedItem == 1
        fileInfos = out.sorted { byCreated ? $0.created > $1.created
                                           : $0.modified > $1.modified }
        table.reloadData()
        if !fileInfos.isEmpty {
            table.selectRowIndexes([0], byExtendingSelection: false)
        }
    }

    func numberOfRows(in tableView: NSTableView) -> Int { fileInfos.count }

    func tableView(_ tv: NSTableView, viewFor col: NSTableColumn?, row: Int) -> NSView? {
        let isDate = col?.identifier.rawValue == "d"
        let id = NSUserInterfaceItemIdentifier(isDate ? "cd" : "cf")
        let tf = tv.makeView(withIdentifier: id, owner: nil) as? NSTextField
            ?? { let t = NSTextField(labelWithString: ""); t.identifier = id
                 t.lineBreakMode = .byTruncatingMiddle; return t }()
        let info = fileInfos[row]
        if isDate {
            let f = DateFormatter()
            f.dateFormat = "MM-dd HH:mm"
            tf.stringValue = f.string(from: sortSel.indexOfSelectedItem == 1
                                      ? info.created : info.modified)
            tf.font = NSFont.monospacedDigitSystemFont(ofSize: 10.5, weight: .regular)
            tf.textColor = .secondaryLabelColor
        } else {
            tf.stringValue = (info.rel as NSString).deletingPathExtension
            tf.font = NSFont.systemFont(ofSize: 12)
            tf.textColor = .labelColor
        }
        return tf
    }

    func tableViewSelectionDidChange(_ n: Notification) {
        loadVersions()
    }

    var selected: String? {
        let r = table.selectedRow
        return (r >= 0 && r < fileInfos.count) ? fileInfos[r].rel : nil
    }

    func loadVersions() {
        versions.removeAllItems()
        commits = []
        guard let rel = selected else { text.string = ""; return }
        versions.addItem(withTitle: "当前版本")
        let log = runGit(["log", "--follow", "--date=format:%m-%d %H:%M",
                          "--format=%H%x09%ad", "--", rel])
        for line in log.split(separator: "\n") {
            let parts = line.split(separator: "\t", maxSplits: 1)
            if parts.count == 2 {
                let sha = String(parts[0])
                commits.append((sha, String(parts[1])))
                versions.addItem(withTitle: "\(parts[1]) 版本（\(sha.prefix(7))）")
            }
        }
        versions.selectItem(at: 0)
        versionChanged()
    }

    @objc func versionChanged() {
        guard let rel = selected else { return }
        let idx = versions.indexOfSelectedItem
        var content: String
        if idx <= 0 {
            content = (try? String(contentsOfFile: syncDir + "/" + rel,
                                   encoding: .utf8)) ?? "(读取失败)"
        } else {
            content = runGit(["show", "\(commits[idx - 1].sha):\(rel)"])
            if content.isEmpty { content = "(该版本中此笔记不存在)" }
        }
        currentContent = stripFrontMatter(content)
        renderContent()
    }

    @objc func modeChanged() { renderContent() }

    func renderContent() {
        let preview = mode.selectedSegment == 0
        web.isHidden = !preview
        textScroll.isHidden = preview
        if preview {
            let r = runEngine(["preview"], input: currentContent)
            let html = (r.status == 0 && !r.out.isEmpty) ? r.out :
                "<!doctype html><meta charset=utf-8><body><p>预览失败（引擎目录：\(escapeHTML(projectDir))）</p>"
                + "<pre>\(escapeHTML(currentContent))</pre>"
            web.loadHTMLString(html, baseURL: nil)
        } else {
            text.string = currentContent
            text.scroll(.zero)
        }
    }
}

// MARK: - reverse-chronological log window

final class LogController: NSObject {
    let window = makeWindow("NoteSync 日志（新的在上面）", 720, 460)
    let text = NSTextView()

    override init() {
        super.init()
        let v = window.contentView!
        let refresh = NSButton(title: "刷新", target: self, action: #selector(reload))
        refresh.frame = NSRect(x: 640, y: 424, width: 70, height: 28)
        refresh.autoresizingMask = [.minXMargin, .minYMargin]
        refresh.bezelStyle = .rounded
        v.addSubview(refresh)
        let sv = NSScrollView(frame: NSRect(x: 0, y: 0, width: 720, height: 418))
        sv.autoresizingMask = [.width, .height]
        sv.hasVerticalScroller = true
        text.frame = NSRect(origin: .zero, size: sv.contentSize)
        text.isEditable = false
        text.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        text.autoresizingMask = [.width]
        text.textContainerInset = NSSize(width: 8, height: 8)
        sv.documentView = text
        v.addSubview(sv)
    }

    func show() {
        reload()
        present(window)
    }

    @objc func reload() {
        let content = (try? String(contentsOfFile: logPath, encoding: .utf8)) ?? ""
        let reversed = content.split(separator: "\n").reversed().joined(separator: "\n")
        text.string = reversed.isEmpty ? "(暂无日志)" : reversed
        text.scroll(.zero)
    }
}

// MARK: - format check window (notesync lint)

final class LintController: NSObject {
    let window = makeWindow("NoteSync 格式检查", 760, 460)
    let text = NSTextView()

    override init() {
        super.init()
        let v = window.contentView!
        let fix = NSButton(title: "修正可自动修正的写法…", target: self, action: #selector(fixAll))
        fix.frame = NSRect(x: 8, y: 424, width: 200, height: 28)
        fix.autoresizingMask = [.maxXMargin, .minYMargin]
        fix.bezelStyle = .rounded
        v.addSubview(fix)
        let refresh = NSButton(title: "刷新", target: self, action: #selector(reload))
        refresh.frame = NSRect(x: 680, y: 424, width: 70, height: 28)
        refresh.autoresizingMask = [.minXMargin, .minYMargin]
        refresh.bezelStyle = .rounded
        v.addSubview(refresh)
        let sv = NSScrollView(frame: NSRect(x: 0, y: 0, width: 760, height: 418))
        sv.autoresizingMask = [.width, .height]
        sv.hasVerticalScroller = true
        text.frame = NSRect(origin: .zero, size: sv.contentSize)
        text.isEditable = false
        text.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        text.autoresizingMask = [.width]
        text.textContainerInset = NSSize(width: 8, height: 8)
        sv.documentView = text
        v.addSubview(sv)
    }

    func show() {
        reload()
        present(window)
    }

    @objc func reload() {
        let r = runEngine(["lint"])
        let head = "按 NoteSync markdown 规范检查 SYNC 里的笔记（MARKDOWN_PROFILE.md）。\n"
            + "fix = 写法不规范，可一键修正（只改写法、不改语义）；"
            + "warn = 在笔记 App 里无法渲染，需要人判断。\n\n"
        text.string = head + (r.status < 0 ? "(引擎启动失败：\(projectDir))" : r.out)
        text.scroll(.zero)
    }

    @objc func fixAll() {
        let alert = NSAlert()
        alert.messageText = "把 fix 类写法改成规范写法？"
        alert.informativeText = "会直接改写 SYNC 里的 md 文件（表格分隔行、* 列表、标题里的 ** 等），"
            + "语义不变；改动随后同步到 Apple Notes 和 Evernote，git 里有历史可恢复。"
        alert.addButton(withTitle: "修正")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        _ = runEngine(["lint", "--fix"])
        reload()
    }
}

// MARK: - app delegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var watch: Process?
    var timer: Timer?
    let statusLine = NSMenuItem(title: "启动中…", action: nil, keyEquivalent: "")
    let settings = SettingsController()
    let browser = BrowserController()
    let logView = LogController()
    let lintView = LintController()

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let img = NSImage(systemSymbolName: "arrow.triangle.2.circlepath",
                             accessibilityDescription: "NoteSync") {
            statusItem.button?.image = img
        } else {
            statusItem.button?.title = "⇄"
        }
        settings.onSave = { [weak self] in self?.restartWatch() }
        buildMenu()
        try? FileManager.default.createDirectory(atPath: stateDir,
                                                 withIntermediateDirectories: true)
        startWatch()
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.ensureAlive()
            self?.refreshStatus()
        }
        try? SMAppService.mainApp.register()
    }

    func buildMenu() {
        let menu = NSMenu()
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        menu.addItem(withTitle: "立即同步", action: #selector(syncNow), keyEquivalent: "s")
        menu.addItem(withTitle: "笔记浏览器…", action: #selector(openBrowser), keyEquivalent: "b")
        menu.addItem(withTitle: "查看日志…", action: #selector(openLog), keyEquivalent: "l")
        menu.addItem(withTitle: "格式检查…", action: #selector(openLint), keyEquivalent: "k")
        menu.addItem(withTitle: "打开 SYNC 文件夹", action: #selector(openSync), keyEquivalent: "o")
        menu.addItem(.separator())
        menu.addItem(withTitle: "设置…", action: #selector(openSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "退出 NoteSync", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        statusItem.menu = menu
    }

    // -- engine child ----------------------------------------------------
    func pythonProcess(_ args: [String]) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-u", "-m", "notesync"] + args
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        p.environment = env
        var logURL = URL(fileURLWithPath: logPath)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if (try? FileHandle(forWritingTo: logURL)) == nil {
            logURL = URL(fileURLWithPath: "/tmp/notesync-bar.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let h = try? FileHandle(forWritingTo: logURL) {
            h.seekToEndOfFile()
            p.standardOutput = h
            p.standardError = h
        }
        return p
    }

    func startWatch() {
        let p = pythonProcess(["watch", "--interval", "300"])
        do { try p.run(); watch = p } catch { watch = nil }
    }

    func restartWatch() {
        watch?.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            self?.startWatch()
        }
    }

    func ensureAlive() {
        if let w = watch, w.isRunning { return }
        startWatch()
    }

    func refreshStatus() {
        guard let data = FileManager.default.contents(atPath: logPath),
              let content = String(data: data, encoding: .utf8) else {
            statusLine.title = watch?.isRunning == true ? "运行中（暂无日志）" : "引擎未运行"
            return
        }
        let lines = content.split(separator: "\n").suffix(120)
        var s = watch?.isRunning == true ? "运行中" : "引擎未运行"
        if let c = lines.last(where: { $0.contains("cycle") }),
           let m = c.range(of: #"cycle[^0-9]*(\d+)"#, options: .regularExpression) {
            let num = c[m].components(separatedBy: CharacterSet.decimalDigits.inverted)
                .joined()
            let ts = c.hasPrefix("[") ? String(c.dropFirst().prefix(14)) : ""
            s += " · 上轮 \(num) 条" + (ts.isEmpty ? "" : " · \(ts)")
        }
        if lines.last(where: { $0.contains("[error]") }) != nil {
            s += " · 有错误(看日志)"
        }
        statusLine.title = s
    }

    // -- actions ---------------------------------------------------------
    @objc func syncNow() {
        let p = pythonProcess(["sync"])
        try? p.run()
        statusLine.title = "同步中…"
    }

    @objc func openBrowser() { browser.show() }
    @objc func openSettings() { settings.show() }
    @objc func openLog() { logView.show() }
    @objc func openLint() { lintView.show() }

    @objc func openSync() {
        NSWorkspace.shared.open(URL(fileURLWithPath: syncDir))
    }

    @objc func quit() {
        watch?.terminate()
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ note: Notification) {
        watch?.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
