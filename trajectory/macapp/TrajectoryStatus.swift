// 轨迹审计线 · 菜单栏状态 app
//
// 设计原则：**app 里不放任何判据**。它只渲染 runner/status_snapshot.py 产出的 JSON。
// 理由：判据全在 runner/checks/ 且各自带自检与正反例；若把「什么叫健康」抄进 Swift，
// 就多出一份没有自检、会和判据侧长歪的实现。已有六次「判据对、适用域错」的教训，
// 不再造第七个。
//
// 健康四态与图标：
//   OK      ●  绿 —— 轮询器活、零积压、错误日志无增长
//   WARN    ▲  黄 —— 有积压 或 告警堆积
//   DOWN    ■  红 —— 轮询器不在 或 执行器报错
//   UNKNOWN ?  灰 —— **取数失败**。不伪装成 OK：「查不到」与「没问题」是两回事。

import AppKit
import Foundation

let snapshotScript = "${TRAJ_HOME}/runner/status_snapshot.py"
let snapshotJSON = "${TRAJ_DATA_DIR}/changeset-audit/状态快照.json"
let refreshSeconds: TimeInterval = 180

struct Status {
    var health = "UNKNOWN"
    var generatedAt = "-"
    var problems: [String] = []
    var pollerAlive = false
    var pollerPid = "-"
    var cursor = -1, dbMaxId = -1, dbTotal = -1, backlog = -1
    var newUnits24h = 0, requeue24h = 0, bArchived24h = 0, exempt24h = 0, drift24h = 0
    var gate24h: [String: Int] = [:]
    var gateHold: [String] = []
    var verdictsTotal = 0
    var lastVerdict = "-"
    var alertsTotal = 0, alertsHandled = 0
    var alertsPending: [String] = []
    var advice: [String: [String: String]] = [:]     // 单号 → {route, why, ...}
    var adviceError: String? = nil
    var loadError: String? = nil
}

func loadStatus() -> Status {
    var s = Status()
    guard let data = FileManager.default.contents(atPath: snapshotJSON),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        s.loadError = "读不到快照文件（还没生成，或上游取数失败）"
        return s
    }
    s.health = obj["health"] as? String ?? "UNKNOWN"
    s.generatedAt = obj["generated_at"] as? String ?? "-"
    s.problems = obj["problems"] as? [String] ?? []
    s.pollerAlive = obj["poller_alive"] as? Bool ?? false
    s.pollerPid = obj["poller_pid"] as? String ?? "-"
    s.cursor = obj["cursor"] as? Int ?? -1
    s.dbMaxId = obj["db_max_id"] as? Int ?? -1
    s.dbTotal = obj["db_total"] as? Int ?? -1
    s.backlog = obj["backlog"] as? Int ?? -1
    s.newUnits24h = obj["new_units_24h"] as? Int ?? 0
    s.requeue24h = obj["requeue_24h"] as? Int ?? 0
    s.bArchived24h = obj["b_archived_24h"] as? Int ?? 0
    s.exempt24h = obj["exempt_24h"] as? Int ?? 0
    s.drift24h = obj["drift_24h"] as? Int ?? 0
    s.gate24h = obj["gate_24h"] as? [String: Int] ?? [:]
    if let holds = obj["gate_hold_24h"] as? [[String: Any]] {
        s.gateHold = holds.compactMap { $0["cs"] as? String }
    }
    s.verdictsTotal = obj["verdicts_total"] as? Int ?? 0
    if let lv = obj["last_verdict"] as? [String: Any] {
        s.lastVerdict = "\(lv["cs"] as? String ?? "-")  \(lv["overall"] as? String ?? "-")"
    }
    s.alertsTotal = obj["alerts_total"] as? Int ?? 0
    s.alertsHandled = obj["alerts_handled"] as? Int ?? 0
    s.alertsPending = obj["alerts_pending"] as? [String] ?? []
    s.adviceError = obj["advice_error"] as? String
    if let adv = obj["alerts_advice"] as? [String: Any] {
        for (k, v) in adv {
            if let d = v as? [String: Any] {
                var m: [String: String] = [:]
                for (kk, vv) in d { m[kk] = "\(vv)" }
                s.advice[k] = m
            }
        }
    }
    return s
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        refresh(runSnapshot: true)
        timer = Timer.scheduledTimer(withTimeInterval: refreshSeconds, repeats: true) { [weak self] _ in
            self?.refresh(runSnapshot: true)
        }
    }

    // 跑一次快照脚本（后台），完成后重绘。脚本自己做原子替换，读不到半截文件。
    func refresh(runSnapshot: Bool) {
        if runSnapshot {
            DispatchQueue.global(qos: .utility).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                p.arguments = ["python3", snapshotScript]
                try? p.run()
                p.waitUntilExit()
                DispatchQueue.main.async { self.render() }
            }
        } else {
            render()
        }
    }

    func render() {
        let s = loadStatus()
        let (glyph, color): (String, NSColor) = {
            switch s.health {
            case "OK":   return ("●", .systemGreen)
            case "WARN": return ("▲", .systemYellow)
            case "DOWN": return ("■", .systemRed)
            default:     return ("?", .systemGray)
            }
        }()
        var label = glyph
        if s.backlog > 0 { label += " \(s.backlog)" }
        if !s.alertsPending.isEmpty { label += " ⚑\(s.alertsPending.count)" }
        statusItem.button?.attributedTitle = NSAttributedString(
            string: label,
            attributes: [.foregroundColor: color,
                         .font: NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .medium)])

        let menu = NSMenu()
        func row(_ t: String, bold: Bool = false, indent: Bool = false) {
            let it = NSMenuItem(title: (indent ? "    " : "") + t, action: nil, keyEquivalent: "")
            if bold { it.attributedTitle = NSAttributedString(
                string: t, attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]) }
            it.isEnabled = false
            menu.addItem(it)
        }

        if let e = s.loadError {
            row("状态未知：\(e)", bold: true)
        } else {
            row("轨迹审计线 · \(s.health)", bold: true)
            row("快照时间 \(s.generatedAt)", indent: true)
            menu.addItem(.separator())

            if s.problems.isEmpty {
                row("无问题", bold: true)
            } else {
                row("需要注意", bold: true)
                for p in s.problems { row("• " + p, indent: true) }
            }
            menu.addItem(.separator())

            row("流水线", bold: true)
            row("轮询器 " + (s.pollerAlive ? "运行中 (pid \(s.pollerPid))" : "**未运行**"), indent: true)
            row("游标 \(s.cursor) / 库最大 \(s.dbMaxId) → 积压 \(s.backlog)", indent: true)
            row("登记单总数 \(s.dbTotal) · 累计判决 \(s.verdictsTotal)", indent: true)
            row("最近判决 \(s.lastVerdict)", indent: true)
            menu.addItem(.separator())

            row("近 24 小时", bold: true)
            row("新单 \(s.newUnits24h) · 漂移重排队 \(s.requeue24h) · 判决失效 \(s.drift24h)", indent: true)
            let gateStr = s.gate24h.map { "\($0.key) \($0.value)" }.sorted().joined(separator: " · ")
            row("A门 " + (gateStr.isEmpty ? "无" : gateStr), indent: true)
            if !s.gateHold.isEmpty { row("A门拦下: " + s.gateHold.joined(separator: ", "), indent: true) }
            row("B门归档 \(s.bArchived24h) · 基线豁免 \(s.exempt24h)", indent: true)
            menu.addItem(.separator())

            row("告警 \(s.alertsHandled)/\(s.alertsTotal) 已处置", bold: true)
            if s.alertsPending.isEmpty {
                row("无未处置", indent: true)
            } else {
                if let ae = s.adviceError { row("（机器建议算不出：\(ae)）", indent: true) }
                for a in s.alertsPending.prefix(15) {
                    let adv = s.advice[a]
                    let route = adv?["route"] ?? "?"
                    let item = NSMenuItem(title: "    \(a)   建议 \(route)", action: nil, keyEquivalent: "")
                    let sub = NSMenu()

                    // 建议理由（只读）
                    if let why = adv?["why"], !why.isEmpty {
                        let w = NSMenuItem(title: why, action: nil, keyEquivalent: "")
                        w.isEnabled = false; sub.addItem(w)
                    }
                    if let m = adv?["merge"], let rc = adv?["recheck"], let ov = adv?["orig"] {
                        let d = NSMenuItem(title: "并线 \(m) · 复核 \(rc) · 原判 \(ov)",
                                           action: nil, keyEquivalent: "")
                        d.isEnabled = false; sub.addItem(d)
                    }
                    sub.addItem(.separator())

                    // 四个带含义的处置动作。**没有「关闭」**——一键关掉而不记原因，
                    // 正是 B 的两条不变式要防的「告警消费闭环变成告警消音器」。
                    for (act, label) in [("confirm", "确认放行（看过，无需行动）"),
                                         ("rework",  "转承建方返工"),
                                         ("trace",   "转轨迹线自查（判据/执行器病）"),
                                         ("later",   "已知·稍后处理（仍计未处置）")] {
                        let mi = NSMenuItem(title: label, action: #selector(disposeAlert(_:)), keyEquivalent: "")
                        mi.target = self
                        mi.representedObject = ["cs": a, "action": act]
                        sub.addItem(mi)
                    }
                    item.submenu = sub
                    menu.addItem(item)
                }
                if s.alertsPending.count > 15 {
                    row("…另 \(s.alertsPending.count - 15) 张", indent: true)
                }
            }
        }

        menu.addItem(.separator())
        let r = NSMenuItem(title: "立即刷新", action: #selector(manualRefresh), keyEquivalent: "r")
        r.target = self; menu.addItem(r)
        let o = NSMenuItem(title: "打开回执目录", action: #selector(openReceipts), keyEquivalent: "o")
        o.target = self; menu.addItem(o)
        menu.addItem(.separator())
        let q = NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(q)
        statusItem.menu = menu
    }

    @objc func manualRefresh() { refresh(runSnapshot: true) }

    // 处置：调 runner/checks/dispose_alert.py。**逻辑不在 app 里**——
    // 它有自检（四动作各写各段、later 不计已处置、两条反例抛错），app 只负责传参。
    @objc func disposeAlert(_ sender: NSMenuItem) {
        guard let info = sender.representedObject as? [String: String],
              let cs = info["cs"], let act = info["action"] else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            p.arguments = ["python3", "${TRAJ_HOME}/runner/checks/dispose_alert.py",
                           cs, "--action", act, "--actor", "Owner（菜单栏）"]
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
            try? p.run(); p.waitUntilExit()
            let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                             encoding: .utf8) ?? ""
            DispatchQueue.main.async {
                let n = NSUserNotification()
                n.title = p.terminationStatus == 0 ? "已处置 \(cs)" : "处置失败 \(cs)"
                n.informativeText = p.terminationStatus == 0
                    ? "动作 \(act)，已写入回执目录（主窗口监听中）"
                    : out.prefix(200).description
                NSUserNotificationCenter.default.deliver(n)
                self.refresh(runSnapshot: true)
            }
        }
    }
    @objc func openReceipts() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "${FLEET_HOME}/<项目>ERP/迁移备份/回执"))
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)      // 只在菜单栏，不进 Dock
app.run()
