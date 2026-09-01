// NoteSyncBar: menu bar host for the notesync engine (DESIGN.md M5).
// TCC "responsible app": the python watch loop runs as our child and
// inherits the Documents / iCloud grants. v1.1 adds a settings window,
// a note browser with git history, and a reverse-chronological log view.
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

func mdToHTML(_ md: String) -> String {
    func esc(_ x: String) -> String {
        x.replacingOccurrences(of: "&", with: "&amp;")
         .replacingOccurrences(of: "<", with: "&lt;")
         .replacingOccurrences(of: ">", with: "&gt;")
    }
    func inline(_ x: String) -> String {
        var t = esc(x)
        t = t.replacingOccurrences(of: #"\*\*(.+?)\*\*"#, with: "<b>$1</b>", options: .regularExpression)
        t = t.replacingOccurrences(of: #"(?<!\*)\*([^*]+)\*(?!\*)"#, with: "<i>$1</i>", options: .regularExpression)
        t = t.replacingOccurrences(of: #"`([^`]+)`"#, with: "<code>$1</code>", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\[([^\]]+)\]\((https?://[^\s)]+)\)"#,
                                   with: "<a href=\"$2\">$1</a>", options: .regularExpression)
        return t
    }
    func cells(_ ln: String) -> [String] {
        var t = ln.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|").map {
            inline($0.trimmingCharacters(in: .whitespaces))
        }
    }
    var out: [String] = []
    var inUl = false, inOl = false
    func closeLists() {
        if inUl { out.append("</ul>"); inUl = false }
        if inOl { out.append("</ol>"); inOl = false }
    }
    let lines = md.components(separatedBy: "\n")
    var i = 0
    while i < lines.count {
        let ln = lines[i]
        if ln.hasPrefix("|"), i + 1 < lines.count,
           lines[i + 1].range(of: #"^\|[-| :]+\|?$"#, options: .regularExpression) != nil {
            closeLists()
            var html = "<table><tr>" + cells(ln).map { "<th>\($0)</th>" }.joined() + "</tr>"
            var j = i + 2
            while j < lines.count, lines[j].hasPrefix("|") {
                html += "<tr>" + cells(lines[j]).map { "<td>\($0)</td>" }.joined() + "</tr>"
                j += 1
            }
            out.append(html + "</table>")
            i = j
            continue
        }
        if ln.hasPrefix("# ") { closeLists(); out.append("<h1>\(inline(String(ln.dropFirst(2))))</h1>") }
        else if ln.hasPrefix("## ") { closeLists(); out.append("<h2>\(inline(String(ln.dropFirst(3))))</h2>") }
        else if ln.hasPrefix("### ") { closeLists(); out.append("<h3>\(inline(String(ln.dropFirst(4))))</h3>") }
        else if ln.hasPrefix("- [ ] ") || ln.hasPrefix("- [x] ") {
            closeLists()
            let checked = ln.hasPrefix("- [x] ") ? " checked" : ""
            out.append("<div class=todo><input type=checkbox disabled\(checked)> \(inline(String(ln.dropFirst(6))))</div>")
        }
        else if ln.hasPrefix("- ") {
            if inOl { out.append("</ol>"); inOl = false }
            if !inUl { out.append("<ul>"); inUl = true }
            out.append("<li>\(inline(String(ln.dropFirst(2))))</li>")
        }
        else if ln.range(of: #"^\d+\. "#, options: .regularExpression) != nil {
            if inUl { out.append("</ul>"); inUl = false }
            if !inOl { out.append("<ol>"); inOl = true }
            let body = String(ln.drop(while: { $0 != " " }).dropFirst())
            out.append("<li>\(inline(body))</li>")
        }
        else if ln.isEmpty { closeLists(); out.append("<div class=blank></div>") }
        else { closeLists(); out.append("<p>\(inline(ln))</p>") }
        i += 1
    }
    closeLists()
    let css = """
    :root { color-scheme: light dark; }
    body { font: 14px/1.7 -apple-system, 'PingFang SC', sans-serif; margin: 0;
           padding: 16px 20px; color: CanvasText; background: Canvas; max-width: 46em; }
    h1 { font-size: 1.5em; margin: .6em 0 .3em; } h2 { font-size: 1.25em; margin: .6em 0 .3em; }
    h3 { font-size: 1.1em; margin: .5em 0 .3em; }
    p { margin: .2em 0; } ul, ol { margin: .2em 0; padding-left: 1.4em; }
    .blank { height: .9em; } .todo { margin: .2em 0; }
    table { border-collapse: collapse; margin: .5em 0; font-size: .95em; }
    th, td { border: 1px solid rgba(127,127,127,.45); padding: 4px 9px; text-align: left; }
    th { background: rgba(127,127,127,.12); }
    code { font-family: ui-monospace, Menlo, monospace; font-size: .9em;
           background: rgba(127,127,127,.15); border-radius: 4px; padding: 1px 5px; }
    a { color: -apple-system-blue; }
    """
    return "<!doctype html><meta charset=utf-8><style>\(css)</style><body>\(out.joined())</body>"
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
            web.loadHTMLString(mdToHTML(currentContent), baseURL: nil)
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

// MARK: - app delegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var watch: Process?
    var timer: Timer?
    let statusLine = NSMenuItem(title: "启动中…", action: nil, keyEquivalent: "")
    let settings = SettingsController()
    let browser = BrowserController()
    let logView = LogController()

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
