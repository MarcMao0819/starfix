import Cocoa

// 请示浮窗 v4（CS-<日期>-ASKPANEL-UI，按增补 2 规格重做）
// 功能与 v3.5 一致：读激活器未答题 → 大按钮选项 + 推荐高亮 + 解锁任务 → 点答只追加一行到收件箱。
// 全部使用 AppKit 语义色与系统控件，无自定义 RGB。
// 路径可用环境变量覆盖（候选版预览用）：ACTIVATOR_JSON / ASK_INBOX
// 路径全部来自环境变量；FLEET_HOME 缺失即退出，不猜默认值（见 scripts/README-env.md）
let fleetHome = ProcessInfo.processInfo.environment["FLEET_HOME"] ?? ""
if fleetHome.isEmpty {
    FileHandle.standardError.write("缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md\n".data(using: .utf8)!)
    exit(2)
}
let dataPath = ProcessInfo.processInfo.environment["FLEET_ACTIVATOR_JSON"]
    ?? ProcessInfo.processInfo.environment["ACTIVATOR_JSON"] ?? "\(fleetHome)/task-activator.json"
let inboxPath = ProcessInfo.processInfo.environment["FLEET_ASK_INBOX"]
    ?? ProcessInfo.processInfo.environment["ASK_INBOX"] ?? "\(fleetHome)/ask-inbox.jsonl"
// 浮窗只列「问指挥官」的题：who 字段含 FLEET_OWNER_NAME（默认 Owner）
let ownerName = ProcessInfo.processInfo.environment["FLEET_OWNER_NAME"] ?? "Owner"

// MARK: - 中英文案（默认中文，记住上次选择；题干与选项来自 JSON，不翻译）

enum Lang: String { case zh, en }
enum L {
    static var cur: Lang = Lang(rawValue: ProcessInfo.processInfo.environment["ASKPANEL_LANG"]
        ?? UserDefaults.standard.string(forKey: "askpanel.lang") ?? "zh") ?? .zh
    static func set(_ l: Lang) { cur = l; UserDefaults.standard.set(l.rawValue, forKey: "askpanel.lang") }
    static func t(_ zh: String, _ en: String) -> String { cur == .zh ? zh : en }
    static func head(_ n: Int, _ k: Int) -> String {
        n == 0 ? t("暂无待答", "Nothing awaiting reply")
               : t("待答 \(n) · 可解锁 \(k)", "\(n) awaiting · unlocks \(k)")
    }
    static var recommended: String { t("推荐", "Recommended") }
    static func unlockAfter(_ s: String) -> String { t("答复后解锁：\(s)", "Unlocks after reply: \(s)") }
    static var noTask: String { t("答复后无关联任务", "No task linked to this reply") }
    static var other: String { t("其他…", "Other…") }
    static var otherHint: String { t("自定义答复", "Your own reply") }
    static var submit: String { t("提交", "Submit") }
    static var expand: String { t("展开", "More") }
    static var collapse: String { t("收起", "Less") }
    static var empty: String { t("暂无待答", "Nothing awaiting reply") }
    static func lastAnswer(_ s: String) -> String { t("上次答复 \(s)", "Last reply \(s)") }
    static var lastAnswerNone: String { t("尚无答复记录", "No reply on record yet") }
    static func missing(_ p: String) -> String { t("激活器文件缺失：\(p)", "Activator file missing: \(p)") }
    static var title: String { t("请示浮窗", "Ask Panel") }
    static var byRec: String { t("按建议办", "Follow the recommendation") }
    static var openPanel: String { t("打开请示浮窗", "Open ask panel") }
    static func notchPending(_ n: Int) -> String { t("\(n) 道待答", "\(n) awaiting") }
    static var refreshTip: String { t("刷新", "Refresh") }
    static func ago(_ d: Date) -> String {
        let s = Int(Date().timeIntervalSince(d))
        if s < 60 { return t("刚刚", "just now") }
        if s < 3600 { return t("\(s / 60) 分钟前", "\(s / 60) min ago") }
        if s < 86400 { return t("\(s / 3600) 小时前", "\(s / 3600) h ago") }
        return t("\(s / 86400) 天前", "\(s / 86400) d ago")
    }
}

// MARK: - 数据

struct Opt { let label: String; let desc: String; let recommended: Bool }
struct Q {
    let qid: String, who: String, question: String, stem: String, detail: String, recommend: String
    let taskCodes: [String], askedAt: String
    var tasks: Int { taskCodes.count }
}

var pending = Set<String>()   // 已点答、等舰长落库

private func rx(_ p: String) -> NSRegularExpression? {
    try? NSRegularExpression(pattern: p, options: [.dotMatchesLineSeparators])
}

/// 题干与内联选项：question 里形如「A=… B=… C=…」的写法
private func splitStem(_ question: String) -> (String, [(String, String)]) {
    let ns = question as NSString
    guard let re = rx("(?:^|[\\s，。、；;）)])([ABCD])\\s*=") else { return (question, []) }
    let ms = re.matches(in: question, range: NSRange(location: 0, length: ns.length))
    guard ms.count >= 2 else { return (question, []) }
    var opts: [(String, String)] = []
    for (i, m) in ms.enumerated() {
        let label = ns.substring(with: m.range(at: 1))
        let from = m.range.location + m.range.length
        let to = i + 1 < ms.count ? ms[i + 1].range.location : ns.length
        let desc = ns.substring(with: NSRange(location: from, length: max(0, to - from)))
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !desc.isEmpty { opts.append((label, desc)) }
    }
    let stem = ns.substring(to: ms[0].range.location).trimmingCharacters(in: .whitespacesAndNewlines)
    return (stem.isEmpty ? question : stem, opts)
}

/// detail 里的「选项：」段，形如「按此办」= …；「先不管」= …
private func detailOptions(_ detail: String) -> [(String, String)] {
    guard let seg = rx("选项[：:]\\s*(.+?)(?:\\n\\s*影响|\\n\\s*背景|$)"),
          let m = seg.firstMatch(in: detail, range: NSRange(location: 0, length: (detail as NSString).length))
    else { return [] }
    let body = (detail as NSString).substring(with: m.range(at: 1))
    var out: [(String, String)] = []
    if let re = rx("「([^」]{1,12})」\\s*=\\s*([^；;\\n]+)") {
        for mm in re.matches(in: body, range: NSRange(location: 0, length: (body as NSString).length)) {
            let l = (body as NSString).substring(with: mm.range(at: 1))
            let d = (body as NSString).substring(with: mm.range(at: 2)).trimmingCharacters(in: .whitespacesAndNewlines)
            out.append((l, d))
        }
    }
    return out
}

private func isRecommended(_ label: String, _ recommend: String) -> Bool {
    if recommend.isEmpty { return false }
    if recommend.contains("「\(label)」") { return true }
    if label.count == 1, let f = label.unicodeScalars.first, CharacterSet.uppercaseLetters.contains(f) {
        for p in ["建议\(label)", "建议 \(label)", "选\(label)", "选 \(label)", "推荐\(label)", "推荐 \(label)", "\(label) 方案", "\(label)方案"] {
            if recommend.contains(p) { return true }
        }
    }
    return false
}

func optionsFor(_ q: Q) -> [Opt] {
    var raw = splitStem(q.question).1
    if raw.isEmpty { raw = detailOptions(q.detail) }
    if raw.isEmpty {
        raw = [("按此办", L.t("照建议或默认做法执行", "Proceed as proposed")),
               ("先不管", L.t("暂不处理，保持现状", "Leave as is for now"))]
    }
    var opts = raw.map { Opt(label: $0.0, desc: $0.1, recommended: isRecommended($0.0, q.recommend)) }
    if !q.recommend.isEmpty && !opts.contains(where: { $0.recommended }) {
        opts.insert(Opt(label: L.byRec, desc: q.recommend, recommended: true), at: 0)
    }
    return opts
}

enum LoadResult { case ok([Q]), missing }

func loadOpen() -> LoadResult {
    guard let d = FileManager.default.contents(atPath: dataPath),
          let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let ds = j["decisions"] as? [[String: Any]] else { return .missing }
    var out: [Q] = []
    for q in ds {
        let ans = (q["answer"] as? String) ?? ""
        if !ans.isEmpty { continue }
        let who = (q["who"] as? String) ?? ""
        if !who.isEmpty && !who.contains(ownerName) { continue }   // 浮窗只列问指挥官的题
        let question = q["question"] as? String ?? ""
        out.append(Q(qid: q["qid"] as? String ?? "?", who: who, question: question,
                     stem: splitStem(question).0, detail: q["detail"] as? String ?? "",
                     recommend: q["recommend"] as? String ?? "",
                     taskCodes: (q["tasks"] as? [Any])?.compactMap { $0 as? String } ?? [],
                     askedAt: q["asked_at"] as? String ?? ""))
    }
    return .ok(out.filter { !pending.contains($0.qid) }.sorted { $0.tasks > $1.tasks })
}

func lastAnswerTime() -> String? {
    if let s = try? String(contentsOfFile: inboxPath, encoding: .utf8) {
        for line in s.split(separator: "\n").filter({ !$0.isEmpty }).reversed() {
            if let d = line.data(using: .utf8),
               let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
               let ts = j["ts"] as? String, !ts.isEmpty { return ts }
        }
    }
    if let d = FileManager.default.contents(atPath: dataPath),
       let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
       let ds = j["decisions"] as? [[String: Any]] {
        return ds.compactMap { $0["answered_at"] as? String }.filter { !$0.isEmpty }.max()
    }
    return nil
}

func appendInbox(qid: String, answer: String) {
    let df = DateFormatter(); df.dateFormat = "yyyy-MM-dd HH:mm:ss"
    let rec: [String: Any] = ["qid": qid, "answer": answer, "ts": df.string(from: Date()), "from": "浮窗"]
    if let data = try? JSONSerialization.data(withJSONObject: rec), let s = String(data: data, encoding: .utf8) {
        if let h = FileHandle(forWritingAtPath: inboxPath) { h.seekToEndOfFile(); h.write((s + "\n").data(using: .utf8)!); h.closeFile() }
        else { try? (s + "\n").write(toFile: inboxPath, atomically: true, encoding: .utf8) }
    }
    pending.insert(qid)
}

func parseTS(_ s: String) -> Date? {
    let df = DateFormatter(); df.locale = Locale(identifier: "en_US_POSIX")
    for f in ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm"] {
        df.dateFormat = f
        if let d = df.date(from: s) { return d }
    }
    return nil
}

// MARK: - 控件

class Flipped: NSView { override var isFlipped: Bool { true } }

func symbol(_ name: String, _ pt: CGFloat, _ weight: NSFont.Weight = .regular) -> NSImage? {
    guard let img = NSImage(systemSymbolName: name, accessibilityDescription: nil) else { return nil }
    return img.withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: pt, weight: weight))
}

/// 「推荐」胶囊：accent 底、白字
final class PillView: NSView {
    override func draw(_ r: NSRect) {
        let p = NSBezierPath(roundedRect: bounds, xRadius: bounds.height / 2, yRadius: bounds.height / 2)
        NSColor.controlAccentColor.setFill(); p.fill()
        let s = L.recommended
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .medium),
            .foregroundColor: NSColor.white
        ]
        let size = s.size(withAttributes: attrs)
        s.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2), withAttributes: attrs)
    }
}

/// 选项行：整行可点，左侧 circle / checkmark.circle.fill，推荐项 accent 描边 + 「推荐」胶囊
final class OptionRow: Flipped {
    private let onTap: () -> Void
    private let rec: Bool
    private var hover = false { didSet { needsDisplay = true } }

    init(width: CGFloat, opt: Opt, onTap: @escaping () -> Void) {
        self.onTap = onTap; self.rec = opt.recommended
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: 40))
        wantsLayer = true; layer?.cornerRadius = 8

        let iconX: CGFloat = 12, textX: CGFloat = 38
        let pillText = L.recommended
        let pillW: CGFloat = rec ? ceil(pillText.size(withAttributes: [.font: NSFont.systemFont(ofSize: 11, weight: .medium)]).width) + 16 : 0
        let textW = width - textX - 12 - pillW

        let icon = NSImageView(frame: NSRect(x: iconX, y: 11, width: 17, height: 17))
        icon.image = symbol(rec ? "checkmark.circle.fill" : "circle", 15, rec ? .semibold : .regular)
        icon.contentTintColor = rec ? .controlAccentColor : .tertiaryLabelColor
        addSubview(icon)

        let title = NSTextField(labelWithString: opt.label)
        title.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        title.textColor = .labelColor
        title.lineBreakMode = .byTruncatingTail
        title.frame = NSRect(x: textX, y: 10, width: max(40, textW), height: 18)
        addSubview(title)

        var h: CGFloat = 10 + 18
        if !opt.desc.isEmpty {
            let d = NSTextField(wrappingLabelWithString: opt.desc)
            d.font = NSFont.systemFont(ofSize: 12)
            d.textColor = .secondaryLabelColor
            d.preferredMaxLayoutWidth = max(40, textW)
            d.maximumNumberOfLines = 3
            let dh = d.sizeThatFits(NSSize(width: max(40, textW), height: 200)).height
            d.frame = NSRect(x: textX, y: h + 1, width: max(40, textW), height: dh)
            addSubview(d); h += dh + 1
        }
        if rec {
            addSubview(PillView(frame: NSRect(x: width - 12 - pillW, y: 10, width: pillW, height: 18)))
        }
        frame.size.height = max(40, h + 10)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func updateTrackingAreas() {
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self))
    }
    override func mouseEntered(with e: NSEvent) { hover = true }
    override func mouseExited(with e: NSEvent) { hover = false }
    override func mouseDown(with e: NSEvent) { onTap() }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }
    override func draw(_ r: NSRect) {
        let p = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 8, yRadius: 8)
        if rec { NSColor.controlAccentColor.withAlphaComponent(0.08).setFill(); p.fill() }
        else if hover { NSColor.quaternaryLabelColor.setFill(); p.fill() }
        if rec { NSColor.controlAccentColor.setStroke(); p.lineWidth = 1; p.stroke() }
    }
}

/// 一题一张卡：.controlBackgroundColor + 1pt .separatorColor 边框，无阴影
final class Card: Flipped {
    let q: Q
    let field = NSTextField()
    private let onAnswer: (String, String) -> Void
    private let onRelayout: () -> Void
    private(set) var expanded: Bool
    private(set) var otherOpen: Bool

    init(q: Q, width: CGFloat, expanded: Bool, otherOpen: Bool,
         onAnswer: @escaping (String, String) -> Void, onRelayout: @escaping () -> Void) {
        self.q = q; self.onAnswer = onAnswer; self.onRelayout = onRelayout
        self.expanded = expanded; self.otherOpen = otherOpen
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: 10))
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.borderWidth = 1
        applyColors()
        build(width: width)
    }
    required init?(coder: NSCoder) { fatalError() }

    private func applyColors() {
        // CGColor 必须在本视图的有效外观下解析，否则深色态会出现白底白字
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
            layer?.borderColor = NSColor.separatorColor.cgColor
        }
    }
    override func viewDidChangeEffectiveAppearance() { applyColors() }
    override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); applyColors() }

    private func build(width: CGFloat) {
        let pad: CGFloat = 16, iw = width - pad * 2
        var y: CGFloat = 16

        // 题号 12pt + 相对时间，同一行
        var head = q.qid
        if let d = parseTS(q.askedAt) { head += " · " + L.ago(d) }
        let title = NSTextField(labelWithString: head)
        title.font = NSFont.systemFont(ofSize: 12)
        title.textColor = .tertiaryLabelColor
        title.frame = NSRect(x: pad, y: y, width: iw, height: 15); addSubview(title)
        y += 15 + 6

        // 题干 15pt semibold，最多 3 行
        let stem = NSTextField(wrappingLabelWithString: expanded ? q.question : q.stem)
        stem.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        stem.textColor = .labelColor
        stem.preferredMaxLayoutWidth = iw
        if !expanded { stem.maximumNumberOfLines = 3 }
        let sh = stem.sizeThatFits(NSSize(width: iw, height: 4000)).height
        stem.frame = NSRect(x: pad, y: y, width: iw, height: sh); addSubview(stem)
        y += sh + 3

        if expanded || q.question.count > q.stem.count || !q.detail.isEmpty {
            let more = NSButton(title: expanded ? L.collapse : L.expand, target: self, action: #selector(toggleExpand))
            more.isBordered = false
            more.font = NSFont.systemFont(ofSize: 11)
            more.contentTintColor = .linkColor
            more.sizeToFit()
            more.frame.origin = NSPoint(x: pad - 2, y: y)
            addSubview(more)
            y += more.frame.height + 1
        }
        if expanded && !q.detail.isEmpty {
            let det = NSTextField(wrappingLabelWithString: q.detail)
            det.font = NSFont.systemFont(ofSize: 12)
            det.textColor = .secondaryLabelColor
            det.preferredMaxLayoutWidth = iw
            let dh = det.sizeThatFits(NSSize(width: iw, height: 8000)).height
            det.frame = NSRect(x: pad, y: y + 2, width: iw, height: dh); addSubview(det)
            y += dh + 6
        }
        y += 6

        // 选项行：行距 6
        for opt in optionsFor(q) {
            let row = OptionRow(width: iw, opt: opt) { [weak self] in
                guard let self else { return }
                let ans = opt.label == L.byRec ? "按建议办：" + self.q.recommend : opt.label
                self.onAnswer(self.q.qid, ans)
            }
            row.frame.origin = NSPoint(x: pad, y: y)
            addSubview(row)
            y += row.frame.height + 6
        }
        y += 2

        // 其他…：折叠一行；展开为输入框 + 提交
        if otherOpen {
            field.placeholderString = L.otherHint
            field.font = NSFont.systemFont(ofSize: 13)
            field.target = self; field.action = #selector(sendTap)
            let send = NSButton(title: L.submit, target: self, action: #selector(sendTap))
            send.bezelStyle = .rounded; send.keyEquivalent = "\r"
            send.font = NSFont.systemFont(ofSize: 12); send.sizeToFit()
            let fw = iw - send.frame.width - 6
            field.frame = NSRect(x: pad, y: y, width: max(90, fw), height: 24)
            send.frame.origin = NSPoint(x: pad + max(90, fw) + 6, y: y - 1)
            addSubview(field); addSubview(send)
            y += 24 + 8
        } else {
            let other = NSButton(title: L.other, target: self, action: #selector(toggleOther))
            other.isBordered = false
            other.font = NSFont.systemFont(ofSize: 12)
            other.contentTintColor = .secondaryLabelColor
            other.sizeToFit()
            other.frame.origin = NSPoint(x: pad - 2, y: y)
            addSubview(other)
            y += other.frame.height + 6
        }

        // 底部 11pt tertiary：答复后解锁
        let foot = NSTextField(labelWithString: q.taskCodes.isEmpty ? L.noTask : L.unlockAfter(q.taskCodes.joined(separator: "、")))
        foot.font = NSFont.systemFont(ofSize: 11)
        foot.textColor = .tertiaryLabelColor
        foot.preferredMaxLayoutWidth = iw
        foot.maximumNumberOfLines = 2
        let fh = foot.sizeThatFits(NSSize(width: iw, height: 60)).height
        foot.frame = NSRect(x: pad, y: y, width: iw, height: fh); addSubview(foot)
        y += fh + 16

        frame.size.height = y
    }

    @objc private func toggleExpand() { expanded.toggle(); onRelayout() }
    @objc private func toggleOther() { otherOpen = true; onRelayout() }
    @objc private func sendTap() {
        let t = field.stringValue.trimmingCharacters(in: .whitespaces)
        if !t.isEmpty { onAnswer(q.qid, t) }
    }
}

/// 刘海下沿那条：点一下开合浮窗（沿用 v3.5 行为）
final class NotchView: NSView {
    var count = 0 { didSet { needsDisplay = true } }
    var hover = false { didSet { needsDisplay = true } }
    var onTap: (() -> Void)?
    override func updateTrackingAreas() {
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self))
    }
    override func mouseEntered(with e: NSEvent) { hover = true }
    override func mouseExited(with e: NSEvent) { hover = false }
    override func mouseDown(with e: NSEvent) { onTap?() }
    override func draw(_ r: NSRect) {
        let tab = NSRect(x: 0, y: 0, width: bounds.width, height: 18)
        let path = NSBezierPath(roundedRect: tab, xRadius: 9, yRadius: 9)
        NSColor.black.withAlphaComponent(count > 0 || hover ? 0.92 : 0.25).setFill(); path.fill()
        let text = count > 0 ? "🛎 " + L.notchPending(count) : (hover ? L.openPanel : "")
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 11, weight: .medium), .foregroundColor: NSColor.white]
        let size = text.size(withAttributes: attrs)
        text.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: 2), withAttributes: attrs)
    }
}

// MARK: - 主体

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let W: CGFloat = 400
    let topBar: CGFloat = 44
    var panel: NSPanel!, notch: NSWindow!, notchView: NotchView!
    var scroll: NSScrollView!, doc: Flipped!, headLabel: NSTextField!, langSeg: NSSegmentedControl!, refreshBtn: NSButton!
    var lastToggle = Date.distantPast
    var lastIds: [String] = []
    var expandedIds = Set<String>(), otherIds = Set<String>()
    var maxH: CGFloat = 600, anchorTopRight = NSPoint.zero

    func windowShouldClose(_ sender: NSWindow) -> Bool { hidePanel(); return false }

    func applicationDidFinishLaunching(_ n: Notification) {
        let scr = NSScreen.main
        let vf = scr?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let sf = scr?.frame ?? vf
        var notchH: CGFloat = 24
        if #available(macOS 12.0, *), let top = scr?.safeAreaInsets.top, top > 0 { notchH = top }
        maxH = sf.height * 0.70
        anchorTopRight = NSPoint(x: vf.maxX - 16, y: vf.maxY - 8)

        let start = NSRect(x: anchorTopRight.x - W, y: anchorTopRight.y - 320, width: W, height: 320)
        panel = NSPanel(contentRect: start,
                        styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel, .fullSizeContentView],
                        backing: .buffered, defer: false)
        panel.title = L.title
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true; panel.hidesOnDeactivate = false; panel.becomesKeyOnlyIfNeeded = true
        panel.minSize = NSSize(width: W, height: 140)
        // 默认跟随系统浅/深色；预览截图可用 ASKPANEL_APPEARANCE=dark|light 固定
        switch ProcessInfo.processInfo.environment["ASKPANEL_APPEARANCE"] {
        case "dark": panel.appearance = NSAppearance(named: .darkAqua)
        case "light": panel.appearance = NSAppearance(named: .aqua)
        default: panel.appearance = nil
        }

        let content = NSVisualEffectView(frame: panel.contentView!.bounds)
        content.material = .sidebar
        content.blendingMode = .behindWindow
        content.state = .active
        content.autoresizingMask = [.width, .height]

        headLabel = NSTextField(labelWithString: "")
        headLabel.font = NSFont.systemFont(ofSize: 13)
        headLabel.textColor = .secondaryLabelColor
        headLabel.autoresizingMask = [.width, .minYMargin]

        langSeg = NSSegmentedControl(labels: ["中", "EN"], trackingMode: .selectOne, target: self, action: #selector(langChanged))
        langSeg.controlSize = .small
        langSeg.font = NSFont.systemFont(ofSize: 11)
        langSeg.selectedSegment = L.cur == .zh ? 0 : 1
        langSeg.sizeToFit()
        langSeg.autoresizingMask = [.minXMargin, .minYMargin]

        refreshBtn = NSButton(image: symbol("arrow.clockwise", 13) ?? NSImage(), target: self, action: #selector(reload))
        refreshBtn.isBordered = false
        refreshBtn.imagePosition = .imageOnly
        refreshBtn.contentTintColor = .secondaryLabelColor
        refreshBtn.toolTip = L.refreshTip
        refreshBtn.autoresizingMask = [.minXMargin, .minYMargin]

        scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: W, height: start.height - topBar))
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false
        scroll.autohidesScrollers = true
        scroll.autoresizingMask = [.width, .height]
        doc = Flipped(frame: NSRect(x: 0, y: 0, width: W, height: 10))
        scroll.documentView = doc

        content.addSubview(headLabel); content.addSubview(langSeg)
        content.addSubview(refreshBtn); content.addSubview(scroll)
        panel.contentView = content
        panel.delegate = self
        layoutChrome(start.height)
        panel.orderFrontRegardless()

        let notchW: CGFloat = 230
        notch = NSWindow(contentRect: NSRect(x: sf.midX - notchW / 2, y: sf.maxY - notchH - 18, width: notchW, height: notchH + 18),
                         styleMask: .borderless, backing: .buffered, defer: false)
        notch.isOpaque = false; notch.backgroundColor = .clear; notch.hasShadow = false; notch.ignoresMouseEvents = false
        notch.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        notch.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        notchView = NotchView(frame: NSRect(x: 0, y: 0, width: notchW, height: notchH + 18))
        notchView.onTap = { [weak self] in self?.togglePanel() }
        notch.contentView = notchView
        notch.orderFrontRegardless()

        reload()
        panel.alphaValue = 1
        panel.orderFrontRegardless()
        Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { _ in self.reload() }
    }

    /// 顶栏 44pt：左统计、右「中|EN」+ 刷新
    private func layoutChrome(_ h: CGFloat) {
        refreshBtn.frame = NSRect(x: W - 34, y: h - 31, width: 22, height: 22)
        langSeg.frame.origin = NSPoint(x: W - 34 - langSeg.frame.width - 8, y: h - 31)
        // 左侧让开窗口按钮（红黄绿）
        headLabel.frame = NSRect(x: 62, y: h - 30, width: W - 34 - langSeg.frame.width - 78, height: 18)
    }

    @objc func langChanged() {
        L.set(langSeg.selectedSegment == 0 ? .zh : .en)
        notchView?.needsDisplay = true
        lastIds = []
        reload()
    }

    @objc func togglePanel() {
        if Date().timeIntervalSince(lastToggle) < 0.45 { return }
        lastToggle = Date()
        if panel.isVisible { hidePanel() } else { showPanel() }
    }
    @objc func showPanel() {
        if panel.isVisible { return }
        panel.alphaValue = 0
        panel.orderFrontRegardless()
        lastIds = []
        reload()
        NSAnimationContext.runAnimationGroup { ctx in ctx.duration = 0.15; self.panel.animator().alphaValue = 1 }
    }
    func hidePanel() {
        if !panel.isVisible { return }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.15; self.panel.animator().alphaValue = 0
        }, completionHandler: { self.panel.orderOut(nil); self.panel.alphaValue = 1 })
    }

    /// 高度随内容，上限屏幕 70%，右上角锚定
    private func fit(_ contentH: CGFloat) {
        let h = min(max(contentH + topBar, 140), maxH)
        panel.setFrame(NSRect(x: anchorTopRight.x - W, y: anchorTopRight.y - h, width: W, height: h), display: true)
        scroll.frame = NSRect(x: 0, y: 0, width: W, height: h - topBar)
        layoutChrome(h)
    }

    @objc func reload() {
        let result = loadOpen()
        guard case .ok(let qs) = result else {
            notchView?.count = 0
            headLabel.stringValue = L.head(0, 0)
            lastIds = ["__missing__"]
            doc.subviews.forEach { $0.removeFromSuperview() }
            let t = NSTextField(wrappingLabelWithString: L.missing(dataPath))
            t.font = NSFont.systemFont(ofSize: 13)
            t.textColor = .systemRed
            t.preferredMaxLayoutWidth = W - 48
            let h = t.sizeThatFits(NSSize(width: W - 48, height: 400)).height
            t.frame = NSRect(x: 24, y: 20, width: W - 48, height: h)
            doc.frame = NSRect(x: 0, y: 0, width: W, height: h + 44)
            doc.addSubview(t)
            fit(h + 44)
            return
        }
        pending = pending.intersection(Set(qs.map { $0.qid }))
        let ids = qs.map { $0.qid }
        notchView?.count = qs.count
        headLabel.stringValue = L.head(qs.count, qs.reduce(0) { $0 + $1.tasks })
        if ids == lastIds && !ids.isEmpty { return }
        let hasNew = !Set(ids).subtracting(Set(lastIds)).isEmpty
        lastIds = ids
        relayout(qs)
        if !panel.isVisible && hasNew { panel.alphaValue = 1; panel.orderFrontRegardless() }
    }

    func relayout(_ qs: [Q]) {
        var drafts: [String: String] = [:]
        for v in doc.subviews {
            if let c = v as? Card {
                if !c.field.stringValue.isEmpty { drafts[c.q.qid] = c.field.stringValue }
                if c.expanded { expandedIds.insert(c.q.qid) } else { expandedIds.remove(c.q.qid) }
                if c.otherOpen { otherIds.insert(c.q.qid) } else { otherIds.remove(c.q.qid) }
            }
        }
        doc.subviews.forEach { $0.removeFromSuperview() }
        let w = W - 32

        if qs.isEmpty {
            let t = NSTextField(labelWithString: L.empty)
            t.font = NSFont.systemFont(ofSize: 15, weight: .medium)
            t.textColor = .labelColor
            t.alignment = .center
            t.frame = NSRect(x: 16, y: 26, width: W - 32, height: 20)
            let sub = NSTextField(labelWithString: lastAnswerTime().map { L.lastAnswer($0) } ?? L.lastAnswerNone)
            sub.font = NSFont.systemFont(ofSize: 12)
            sub.textColor = .secondaryLabelColor
            sub.alignment = .center
            sub.frame = NSRect(x: 16, y: 50, width: W - 32, height: 16)
            doc.addSubview(t); doc.addSubview(sub)
            doc.frame = NSRect(x: 0, y: 0, width: W, height: 96)
            fit(96)
            fadeIn(doc)
            return
        }

        var y: CGFloat = 8
        for q in qs {
            let c = Card(q: q, width: w, expanded: expandedIds.contains(q.qid), otherOpen: otherIds.contains(q.qid),
                         onAnswer: { [weak self] qid, a in
                             appendInbox(qid: qid, answer: a)
                             self?.lastIds = []
                             self?.reload()
                         },
                         onRelayout: { [weak self] in
                             guard let self, case .ok(let cur) = loadOpen() else { return }
                             self.relayout(cur)
                         })
            if let d = drafts[q.qid] { c.field.stringValue = d }
            c.frame.origin = NSPoint(x: 16, y: y)
            doc.addSubview(c)
            y += c.frame.height + 12
        }
        y += 4
        doc.frame = NSRect(x: 0, y: 0, width: W, height: y)
        fit(y)
        fadeIn(doc)
    }

    private func fadeIn(_ v: NSView) {
        v.wantsLayer = true
        v.layer?.removeAnimation(forKey: "fade")
        let a = CABasicAnimation(keyPath: "opacity")
        a.fromValue = 0.0; a.toValue = 1.0; a.duration = 0.15
        v.layer?.add(a, forKey: "fade")
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
