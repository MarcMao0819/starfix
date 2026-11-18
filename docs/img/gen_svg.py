# 生成机制图（SVG）。风格：白卡片+轻阴影、泳道、圆号步骤、正交圆角箭头、单一强调色。
# 双语：默认 --lang zh，输出 docs/img/*.svg（与改造前逐字节一致）；
#       --lang en 走下面的 TR 词典，输出 docs/img/en/*.svg。
import html, os, sys
E=html.escape; W=1000
LANG='zh'
if '--lang' in sys.argv:
    LANG=sys.argv[sys.argv.index('--lang')+1]
if LANG not in ('zh','en'):
    sys.exit(f'unknown --lang {LANG}（只支持 zh / en）')
# 配色：默认 --theme light；--theme dark 输出到同级 dark/ 目录，给深色页面用。
THEME='light'
if '--theme' in sys.argv:
    THEME=sys.argv[sys.argv.index('--theme')+1]
if THEME not in ('light','dark'):
    sys.exit(f'unknown --theme {THEME}（只支持 light / dark）')

# 英文文案内嵌在这里；键=中文原文，值=英文。缺键则原样输出。
TR={
 # 图1
 '图 1 · 角色与基建':'Fig 1 · Roles and infrastructure',
 '一个舰长（AI 主窗）在 Owner 与舰队之间，只做判断':'One captain (the AI main window) sits between the Owner and the fleet, and only judges',
 '指挥':'COMMAND', '舰长':'CAPTAIN', '舰员':'CREW',
 'Owner / 指挥官':'Owner', '只答确认题；三类事才上升':'Answers confirmation questions only; three kinds escalate',
 '舰长（主窗）':'Captain (main window)', '派单 · 复审 · 验收 · 并线 · 部署 · 对接':'dispatch · review · acceptance · merge · deploy · liaison',
 '对接人（IM）':'Stakeholders (IM)', '质量 / 生产 / 财务 / 商务':'QA / production / finance / commercial',
 '舰员 A':'Crew A', 'Codex CLI · 终端':'Codex CLI · terminal',
 '舰员 B':'Crew B', 'Claude · 总线':'Claude · agent bus',
 '舰员 C':'Crew C', '无头工人机 · 批处理':'headless worker · batch',
 '决策面板 / 收件箱':'decision panel / inbox', '秒回·只发相关岗位':'ack in seconds · right role',
 '任务书 / 回执':'task book / receipt',
 '基建：任务激活器 · 回执目录 · 监听 · 终端投递 · 请示浮窗 · 上下文交接 hook · 轨迹编译（机检）':
   'Infrastructure: task activator · receipt directory · monitors · terminal delivery · ask panel · context-handoff hooks · Trajectory Compiler',
 # 图2
 '图 2 · 一张单从派出到上线':'Fig 2 · One task, from dispatch to production',
 '每一步都落文件；换掉任何一个人（含舰长）流程仍在':'Every step lands a file; replace anyone (the captain included) and the process still stands',
 '监听 / 机检':'MONITORS / CHECKS', '并线 · 部署':'MERGE · DEPLOY',
 '写任务书':'Write the task book', '上下文贴入 · 收尾条款逐字':'context pasted in · closing clause verbatim',
 '投递':'Deliver', '总线 / 终端两步读回':'bus / terminal two-step read-back',
 '承建 + 登记':'Build + register', '独立 worktree · 精确提交':'isolated worktree · exact-path commits',
 '回执落盘':'Receipt lands',
 '回执监听叫醒':'Receipt monitor wakes', '① 监听 → 舰长':'① monitor → captain',
 '抽查 + 派互审':'Spot-check + cross-review', '回执≠事实，实物才是':'a receipt is not a fact; the artifact is',
 '更新舰员画像':'Update crew profile', '完成 / 返工 / 停手 / 自伤':'done/rework/stop/self-caused',
 '并线过门':'Merge through gates', '两道门 · 契约 · 行尾':'two gates · contract · line endings',
 '部署':'Deploy → heartbeat', '迁移→停→换→起→心跳':'migrate→stop→swap→start',
 '版本真相 · 漂移自清':'Version truth / drift', '机检 p6':'self-clears · check p6',
 '文件：CS-<日期>-<代号>-任务书.md → 回执/CS-<日期>-<代号>.md → 登记表 → 版本真相文件；脚本：send-to-session / task-activator / merge / deploy 剧本':
   'Files: CS-<date>-<code>-task.md → receipts/CS-<date>-<code>.md → registration table → version-truth file; scripts: send-to-session / task-activator / merge / deploy runbook',
 # 图3
 '图 3 · 决策闭环':'Fig 3 · The decision loop',
 '等 Owner 的问题都在面板里；答复即解锁':'Every question waiting on the Owner is on the panel; an answer unlocks tasks',
 '舰长 ask add':'Captain: ask add', '题干 + A/B/C + 推荐':'question + A/B/C + recommendation',
 '请示浮窗 / IM':'Ask panel / IM', 'Owner 点答（离席双写）':'Owner taps (double-written when away)',
 '只追加，禁原地改写':'append-only, never rewritten in place',
 '任务解锁':'Task unlocked', 'blocker 清空 → 通知舰长':'blocker cleared → captain notified',
 '离席模式：同一题同时发 IM，两边答复落同一收件箱；凭据类答复落库前抹值。':
   'Away mode: the same question also goes to IM; both channels land in the same inbox; credential answers are blanked before landing.',
 # 图4
 '图 4 · 感官：只报危险侧、只报状态跃迁、不轮询':'Fig 4 · Senses: dangerous side only, transitions only, never poll',
 '每个监听要答得出「它红过吗」':'Every monitor must be able to answer "has it ever gone red?"',
 '回执监听':'Receipt monitor', '回执目录新文件':'new file in the receipt directory',
 '收件箱落库':'Inbox apply', '答复 → 解锁':'answer → unlock',
 '断流看门狗':'Disconnect watchdog', '舰员状态跃迁':'crew state transition',
 '停工哨兵':'Stall sentinel', '在飞静默 > 60 min':'in flight, silent > 60 min',
 'IM 人类回复':'IM human replies', '游标接续':'cursor continuation',
 '机检轮询器':'Machine-check poller', '漂移 / 门禁 / 路由 404':'drift / gate / route 404',
 '业务健康':'Business health', '连续 FAILED · 停摆':'consecutive FAILED · stalled',
 '整点报':'Hourly report', '施工中 / 待开工 / ⚠ 未处置':'in progress / pending / ⚠ unhandled',
 '额度闸':'Usage gate', '≥95% 停派':'≥95% stop dispatching',
 '上下文水位':'Context watermark', '≥95% → 写交接':'≥95% → write the handoff',
 '收到即处置：派单 / 裁定 / 上升':'act on arrival: dispatch / rule / escalate',
 '没有 Monitor 类工具时的兜底四序：后台脚本写事件文件 → 系统定时器投递 → 委托旁窗转发 → 整点手动巡场。':
   'No Monitor tool? Four-step fallback: background script writes event files → system timer → side window forwards → manual hourly patrol.',
 # 图5
 '图 5 · 舰长自己会被压缩：交接怎么不丢':'Fig 5 · The captain gets compacted too: how the handoff survives',
 '按 session_id 分文件，别的会话不受影响':'Files are split by session_id; other sessions are untouched',
 '状态栏快照':'Status-line snapshot',
 '水位 ≥95%':'Watermark ≥95%', 'ctx-watch → 事件 CTX95':'ctx-watch → CTX95 event',
 '手写交接段':'Hand-written handoff', '判断 · 在飞 · 待答 · 下一步':'judgement · in flight · pending · next',
 '机械快照':'Mechanical snapshot', '激活器 · 待答 · 台账 · 监听':'activator · pending · ledger · monitors',
 '压缩前再刷一次兜底':'refreshed once more before compaction',
 '压缩后注入 → 续接':'injected after compaction → carry on',
 'PostCompact 把压缩摘要留档：事后能审「丢了什么」。判据：接手者 10 分钟内不问人能派出第一单。':
   'PostCompact archives the compaction summary — audit what was lost. Criterion: the successor dispatches the first task in 10 min without asking.',
 # 图6
 '图 6 · 轨迹编译：把人审的轨迹编译成机器可复放的图':'Fig 6 · Trajectory Compiler: a human audit trajectory compiled into a machine-replayable graph',
 '稳定判点机械化，LLM 只处理异常分支；每条判据配反向断言':'Stable verdict points go mechanical; the LLM sees only abnormal branches; every criterion carries a reverse assertion',
 '采集':'CAPTURE', '复放':'REPLAY', '输出':'OUTPUT',
 '审计轨迹':'Audit trajectory', '审一张单的每一步，每步输出 verdict=':'every step of one audit emits verdict=',
 '归纳器 inducer':'Inducer', '历史轨迹 → 节点图 + 判点稳定度':'history → node graph + point stability',
 '工作流图 graph vN':'Workflow graph vN', 'nodes + verdict_rules（p1…p7）':'nodes + verdict_rules (p1…p7)',
 '轮询器 audit-poller':'Poller audit-poller', '每分钟取新登记 / 部署 / 路由':'every minute: registration / deploy / route',
 '运行器 run_graph':'Runner run_graph', '按图执行，机械判点直接出 verdict':'runs the graph; mechanical points emit verdict',
 '异常分支 → LLM':'Abnormal branch → LLM', '不稳定分支才交模型，附上下文':'only unstable branches, with context',
 '告警落盘 + 自清':'Alerts land + self-clear', '祖先判据：head ∈ LIVE 即清':'ancestor rule: head ∈ LIVE clears it',
 '回执机检':'Receipt machine check', '三态 / 证据段 / 关键词 / 路径':'states / evidence / keyword / path',
 '新判据回流':'New criteria flow back', '事故 → 夹具（含反向）→ 图 vN+1':'incident → fixture (reverse) → graph vN+1',
 'p1 提交哈希存在 · p2 三方文件数 · p3 文件清单 DB=git · p4 标题乱码 · p5 行尾双规则 · p6 部署漂移 · p7 路由存活':
   'p1 commit hash exists · p2 three-way file count · p3 file list DB=git · p4 garbled title · p5 line-ending rule · p6 deploy drift · p7 route liveness',
}
def T(x):
    if LANG=='zh' or x is None: return x
    return TR.get(x,x)
C={'ink':'#1B1F27','sub':'#6B7280','line':'#9AA3B2','acc':'#2563EB','accbg':'#EAF1FF','lane':'#F3F5F9','bg':'#FFFFFF','ok':'#059669','warn':'#D97706','canvas':'#FFFFFF','stroke':'#E3E7EE'}
if THEME=='dark':
    C={'ink':'#E6EDF3','sub':'#9198A1','line':'#6E7681','acc':'#4493F8','accbg':'#12233D','lane':'#161B22','bg':'#21262D','ok':'#3FB950','warn':'#D29922','canvas':'#0D1117','stroke':'#30363D'}
class G:
    def __init__(s,h,title,sub=None):
        s.h=h; s.o=[]; s.title=T(title); s.sub=T(sub)
    def lane(s,y,h,label):
        s.o.append(f'<rect x="20" y="{y}" width="{W-40}" height="{h}" rx="12" fill="{C["lane"]}"/>')
        s.o.append(f'<text x="34" y="{y+22}" font-size="12" font-weight="700" fill="{C["sub"]}" letter-spacing="1">{E(T(label))}</text>')
    def card(s,x,y,w,h,title,sub=None,n=None,accent=False,color=None):
        col=color or C['acc']
        s.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{C["bg"]}" stroke="{col if accent else C["stroke"]}" stroke-width="{1.5 if accent else 1}" filter="url(#sh)"/>')
        s.o.append(f'<rect x="{x}" y="{y+10}" width="4" height="{h-20}" rx="2" fill="{col}"/>')
        tx=x+18
        if n is not None:
            s.o.append(f'<circle cx="{x+26}" cy="{y+h/2}" r="11" fill="{col}"/><text x="{x+26}" y="{y+h/2+4}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">{n}</text>'); tx=x+46
        cy=y+h/2+(5 if not sub else -3)
        s.o.append(f'<text x="{tx}" y="{cy}" font-size="14" font-weight="600" fill="{C["ink"]}">{E(T(title))}</text>')
        if sub: s.o.append(f'<text x="{tx}" y="{cy+18}" font-size="11.5" fill="{C["sub"]}">{E(T(sub))}</text>')
    def arr(s,pts,label=None,dash=False,color=None):
        col=color or C['line']; d=' stroke-dasharray="5 4"' if dash else ''
        path='M'+' L'.join(f'{x},{y}' for x,y in pts)
        s.o.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="1.6" stroke-linejoin="round" marker-end="url(#m)"{d}/>')
        if label:
            label=T(label)
            (x1,y1),(x2,y2)=pts[0],pts[-1]; mx,my=(x1+x2)/2,(y1+y2)/2; wl=len(label)*(12.5 if LANG=='zh' else 6.8)+10
            s.o.append(f'<rect x="{mx-wl/2}" y="{my-10}" width="{wl}" height="20" rx="10" fill="{C["accbg"]}"/><text x="{mx}" y="{my+4}" text-anchor="middle" font-size="11.5" fill="{C["acc"]}">{E(label)}</text>')
    def note(s,y,t): s.o.append(f'<text x="24" y="{y}" font-size="12" fill="{C["sub"]}">{E(T(t))}</text>')
    def save(s,p):
        if LANG!='zh':
            p=os.path.join(os.path.dirname(p),LANG,os.path.basename(p))
            os.makedirs(os.path.dirname(p),exist_ok=True)
        if THEME=='dark':
            p=os.path.join(os.path.dirname(p),'dark',os.path.basename(p))
            os.makedirs(os.path.dirname(p),exist_ok=True)
        head=(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {s.h}" width="{W}" height="{s.h}" font-family="-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Hiragino Sans GB\',\'Noto Sans CJK SC\',\'Helvetica Neue\',Arial,sans-serif">'
              '<defs><filter id="sh" x="-5%" y="-5%" width="110%" height="120%"><feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#0F172A" flood-opacity="0.08"/></filter>'
              f'<marker id="m" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{C["line"]}"/></marker></defs>'
              f'<rect width="{W}" height="{s.h}" fill="{C["canvas"]}"/><text x="24" y="30" font-size="17" font-weight="700" fill="{C["ink"]}">{E(s.title)}</text>')
        if s.sub: head+=f'<text x="24" y="50" font-size="12" fill="{C["sub"]}">{E(s.sub)}</text>'
        open(p,'w',encoding='utf-8').write(head+''.join(s.o)+'</svg>')

# 图1 角色与基建（三泳道）
g=G(400,'图 1 · 角色与基建','一个舰长（AI 主窗）在 Owner 与舰队之间，只做判断')
g.lane(64,84,'指挥'); g.card(360,78,280,56,'Owner / 指挥官','只答确认题；三类事才上升',accent=True)
g.lane(160,84,'舰长'); g.card(360,174,280,56,'舰长（主窗）','派单 · 复审 · 验收 · 并线 · 部署 · 对接',accent=True)
g.card(700,174,270,56,'对接人（IM）','质量 / 生产 / 财务 / 商务',color=C['ok'])
g.lane(256,84,'舰员'); 
for i,(t,sb) in enumerate([('舰员 A','Codex CLI · 终端'),('舰员 B','Claude · 总线'),('舰员 C','无头工人机 · 批处理')]): g.card(60+i*320,270,280,56,t,sb)
g.arr([(500,134),(500,174)],'决策面板 / 收件箱'); g.arr([(640,202),(700,202)],'秒回·只发相关岗位')
for i in range(3): g.arr([(500,230),(500,250),(200+i*320,250),(200+i*320,270)],'任务书 / 回执' if i==2 else None)
g.note(380,'基建：任务激活器 · 回执目录 · 监听 · 终端投递 · 请示浮窗 · 上下文交接 hook · 轨迹编译（机检）')
g.save('docs/img/01-roles.svg')

# 图2 一张单的生命周期（四泳道）
g=G(470,'图 2 · 一张单从派出到上线','每一步都落文件；换掉任何一个人（含舰长）流程仍在')
lanes=[('舰长',64),('舰员',164),('监听 / 机检',264),('并线 · 部署',364)]
for n,y in lanes: g.lane(y,84,n)
g.card(60,78,200,56,'写任务书','上下文贴入 · 收尾条款逐字',1); g.card(300,78,200,56,'投递','总线 / 终端两步读回',2)
g.card(60,178,200,56,'承建 + 登记','独立 worktree · 精确提交',3); g.card(300,178,200,56,'回执落盘','PASS / FAIL / BLOCKED',4)
g.card(300,278,200,56,'回执监听叫醒','① 监听 → 舰长',5,color=C['warn'])
g.card(540,78,200,56,'抽查 + 派互审','回执≠事实，实物才是',6); g.card(780,78,190,56,'更新舰员画像','完成 / 返工 / 停手 / 自伤',7)
g.card(540,378,200,56,'并线过门','两道门 · 契约 · 行尾',8); g.card(780,378,190,56,'部署','迁移→停→换→起→心跳',9)
g.card(780,278,190,56,'版本真相 · 漂移自清','机检 p6',10,color=C['warn'])
g.arr([(260,106),(300,106)]); g.arr([(400,134),(400,160),(160,160),(160,178)]); g.arr([(260,206),(300,206)]); g.arr([(400,234),(400,278)])
g.arr([(500,306),(520,306),(520,106),(540,106)]); g.arr([(740,106),(780,106)]); g.arr([(640,134),(640,378)]); g.arr([(740,406),(780,406)]); g.arr([(875,378),(875,334)],dash=True)
g.note(462,'文件：CS-<日期>-<代号>-任务书.md → 回执/CS-<日期>-<代号>.md → 登记表 → 版本真相文件；脚本：send-to-session / task-activator / merge / deploy 剧本')
g.save('docs/img/02-task-lifecycle.svg')

# 图3 决策闭环（环形六步）
g=G(330,'图 3 · 决策闭环','等 Owner 的问题都在面板里；答复即解锁')
seq=[('舰长 ask add','题干 + A/B/C + 推荐'),('task-activator.json','decisions[]'),('请示浮窗 / IM','Owner 点答（离席双写）'),('ask-inbox.jsonl','只追加，禁原地改写'),('ask-inbox-apply.sh','tail -F → ask answer'),('任务解锁','blocker 清空 → 通知舰长')]
pos=[(40,70),(370,70),(700,70),(700,200),(370,200),(40,200)]
for i,((t,sb),(x,y)) in enumerate(zip(seq,pos)): g.card(x,y,260,56,t,sb,i+1,accent=i in(0,5))
g.arr([(300,98),(370,98)]); g.arr([(630,98),(700,98)]); g.arr([(830,126),(830,200)]); g.arr([(700,228),(630,228)]); g.arr([(370,228),(300,228)]); g.arr([(170,200),(170,126)],dash=True)
g.note(300,'离席模式：同一题同时发 IM，两边答复落同一收件箱；凭据类答复落库前抹值。')
g.save('docs/img/03-decision-loop.svg')

# 图4 感官（两列进舰长）
g=G(380,'图 4 · 感官：只报危险侧、只报状态跃迁、不轮询','每个监听要答得出「它红过吗」')
L=[('回执监听','回执目录新文件'),('收件箱落库','答复 → 解锁'),('断流看门狗','舰员状态跃迁'),('停工哨兵','在飞静默 > 60 min'),('IM 人类回复','游标接续')]
R=[('机检轮询器','漂移 / 门禁 / 路由 404'),('业务健康','连续 FAILED · 停摆'),('整点报','施工中 / 待开工 / ⚠ 未处置'),('额度闸','≥95% 停派'),('上下文水位','≥95% → 写交接')]
for i,(t,sb) in enumerate(L): g.card(40,64+i*58,260,48,t,sb,color=C['warn'])
for i,(t,sb) in enumerate(R): g.card(700,64+i*58,260,48,t,sb,color=C['warn'])
g.card(380,178,240,64,'舰长（主窗）','收到即处置：派单 / 裁定 / 上升',accent=True)
for i in range(5): g.arr([(300,88+i*58),(340,88+i*58),(340,210),(380,210)]); g.arr([(700,88+i*58),(660,88+i*58),(660,210),(620,210)])
g.note(360,'没有 Monitor 类工具时的兜底四序：后台脚本写事件文件 → 系统定时器投递 → 委托旁窗转发 → 整点手动巡场。')
g.save('docs/img/04-monitors.svg')

# 图5 上下文交接（时间线）
g=G(330,'图 5 · 舰长自己会被压缩：交接怎么不丢','按 session_id 分文件，别的会话不受影响')
seq=[('状态栏快照','ctx-snapshot/<sid>.json'),('水位 ≥95%','ctx-watch → 事件 CTX95'),('手写交接段','判断 · 在飞 · 待答 · 下一步'),('机械快照','激活器 · 待答 · 台账 · 监听'),('PreCompact hook','压缩前再刷一次兜底'),('SessionStart hook','压缩后注入 → 续接')]
for i,(t,sb) in enumerate(seq):
    x=40+(i%3)*320; y=70+(i//3)*120; g.card(x,y,270,56,t,sb,i+1,accent=i in(1,5))
    if i%3<2: g.arr([(x+270,y+28),(x+320,y+28)])
g.arr([(815,126),(815,160),(175,160),(175,190)])
g.note(300,'PostCompact 把压缩摘要留档：事后能审「丢了什么」。判据：接手者 10 分钟内不问人能派出第一单。')
g.save('docs/img/05-context-handoff.svg')

# 图6 轨迹编译
g=G(420,'图 6 · 轨迹编译：把人审的轨迹编译成机器可复放的图','稳定判点机械化，LLM 只处理异常分支；每条判据配反向断言')
g.lane(64,90,'采集'); g.card(60,80,270,56,'审计轨迹','审一张单的每一步，每步输出 verdict=',1)
g.card(380,80,270,56,'归纳器 inducer','历史轨迹 → 节点图 + 判点稳定度',2)
g.card(700,80,270,56,'工作流图 graph vN','nodes + verdict_rules（p1…p7）',3)
g.lane(170,90,'复放'); g.card(60,186,270,56,'轮询器 audit-poller','每分钟取新登记 / 部署 / 路由',4,color=C['warn'])
g.card(380,186,270,56,'运行器 run_graph','按图执行，机械判点直接出 verdict',5,color=C['warn'])
g.card(700,186,270,56,'异常分支 → LLM','不稳定分支才交模型，附上下文',6,color=C['warn'])
g.lane(276,90,'输出'); g.card(60,292,270,56,'告警落盘 + 自清','祖先判据：head ∈ LIVE 即清',7,color=C['ok'])
g.card(380,292,270,56,'回执机检','三态 / 证据段 / 关键词 / 路径',8,color=C['ok'])
g.card(700,292,270,56,'新判据回流','事故 → 夹具（含反向）→ 图 vN+1',9,color=C['ok'])
g.arr([(330,108),(380,108)]); g.arr([(650,108),(700,108)]); g.arr([(835,136),(835,160),(515,160),(515,186)])
g.arr([(330,214),(380,214)]); g.arr([(650,214),(700,214)]); g.arr([(515,242),(515,292)]); g.arr([(195,242),(195,292)]); g.arr([(835,242),(835,292)]); g.arr([(835,348),(835,370),(60,370),(60,108)],dash=True)
g.note(410,'p1 提交哈希存在 · p2 三方文件数 · p3 文件清单 DB=git · p4 标题乱码 · p5 行尾双规则 · p6 部署漂移 · p7 路由存活')
g.save('docs/img/06-trajectory.svg')
# 首页总览图：不带「图 N」标题，宽 1200；负责人 ⇄ 舰长 ⇄ 舰员，底下一条「每一步都落成文件」
TR.update({
 '你（负责人）':'You (the Owner)', '只答选择题':'answer multiple-choice only',
 '只有三类事会来问你':'Only three things escalate to you',
 '· 不可逆的上线操作':'· irreversible production changes',
 '· 只有人知道的业务事实':'· facts only a human knows',
 '· 对外发布':'· anything published externally',
 '选择题＋推荐':'A/B/C question', '一键答复':'one-click answer',
 '一个长期在线的 AI 会话':'one long-running AI session',
 '写任务书，派给合适的舰员':'task books and dispatch',
 '验收实物，安排交叉复审':'acceptance on artifacts, cross-review',
 '合并、上线、写交接':'merge, deploy, handoff',
 '只做判断，不写业务代码':'judges only — writes no product code',
 '任务书':'task book', '回执':'receipt',
 '舰员 · 终端里的 AI 会话':'CREW · AI in terminals',
 '舰员甲':'Crew A', '舰员乙':'Crew B', '舰员丙':'Crew C',
 '模型 A · 独立工作区':'model A · own worktree', '模型 B · 独立工作区':'model B · own worktree', '模型 C · 独立工作区':'model C · own worktree',
 '多家模型混编，互相复审':'mixed vendors, cross-reviewing',
 '每一步都落成文件':'EVERY STEP LANDS AS A FILE',
 '变更登记':'registration', '合并门禁':'merge gates', '上线记录':'deploy record',
 '换掉任何一个会话（包括舰长），':'Swap out any session, the captain',
 '项目照样接得上。':'included, and the project carries on.',
})
def hero(path):
    HW,HH=1200,494; o=[]
    def t(x,y,s,size=14,weight=400,fill=None,anchor='start',ls=None):
        a=f' letter-spacing="{ls}"' if ls else ''
        o.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or C["ink"]}" text-anchor="{anchor}"{a}>{E(T(s))}</text>')
    def box(x,y,w,h,fill,stroke,sw=1,rx=12,shadow=True):
        f=' filter="url(#sh)"' if shadow else ''
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{f}/>')
    def arrow(x1,x2,y,label,above):
        o.append(f'<path d="M{x1},{y} L{x2},{y}" fill="none" stroke="{C["line"]}" stroke-width="1.8" marker-end="url(#m)"/>')
        t((x1+x2)/2, y-10 if above else y+22, label, 13.5, 600, C['acc'], 'middle')
    # 负责人
    box(32,40,264,92,C['bg'],C['stroke'])
    o.append(f'<rect x="32" y="54" width="4" height="64" rx="2" fill="{C["ok"]}"/>')
    t(56,80,'你（负责人）',19,700); t(56,108,'只答选择题',14.5,400,C['sub'])
    t(36,172,'只有三类事会来问你',13,700,C['sub'])
    for i,x in enumerate(['· 不可逆的上线操作','· 只有人知道的业务事实','· 对外发布']): t(36,200+i*26,x,14,400,C['sub'])
    # 舰长
    box(424,40,384,280,C['bg'],C['acc'],2,14)
    o.append(f'<text x="452" y="92" font-size="30" font-weight="700" fill="{C["ink"]}">{"舰长" if LANG=="zh" else "Captain"}</text>')
    t(452,118,'一个长期在线的 AI 会话',14.5,400,C['sub'])
    o.append(f'<path d="M452,136 L780,136" stroke="{C["stroke"]}" stroke-width="1"/>')
    for i,x in enumerate(['写任务书，派给合适的舰员','验收实物，安排交叉复审','合并、上线、写交接']):
        o.append(f'<circle cx="457" cy="{161+i*32}" r="3.5" fill="{C["acc"]}"/>'); t(472,167+i*32,x,16.5,500)
    o.append(f'<rect x="452" y="256" width="328" height="38" rx="19" fill="{C["accbg"]}"/>')
    t(616,281,'只做判断，不写业务代码',14.5,700,C['acc'],'middle')
    # 舰员
    o.append(f'<rect x="936" y="40" width="232" height="280" rx="14" fill="{C["lane"]}" stroke="{C["stroke"]}" stroke-width="1"/>')
    t(952,66,'舰员 · 终端里的 AI 会话',12.5,700,C['sub'],ls=0.5)
    for i,(a,b) in enumerate([('舰员甲','模型 A · 独立工作区'),('舰员乙','模型 B · 独立工作区'),('舰员丙','模型 C · 独立工作区')]):
        y=80+i*66; box(950,y,204,58,C['bg'],C['stroke'],1,10)
        o.append(f'<rect x="950" y="{y+11}" width="4" height="36" rx="2" fill="{C["acc"]}"/>')
        t(968,y+25,a,16,600); t(968,y+45,b,13,400,C['sub'])
    t(1052,302,'多家模型混编，互相复审',13,400,C['sub'],'middle')
    # 往返
    arrow(424,304,68,'选择题＋推荐',True); arrow(296,416,104,'一键答复',False)
    arrow(808,928,160,'任务书',True); arrow(936,816,204,'回执',False)
    # 落文件
    o.append(f'<rect x="32" y="352" width="1136" height="110" rx="14" fill="{C["lane"]}" stroke="{C["stroke"]}" stroke-width="1"/>')
    t(52,380,'每一步都落成文件',12.5,700,C['sub'],ls=0.5)
    chips=['任务书','回执','变更登记','合并门禁','上线记录']
    for i,x in enumerate(chips):
        cx=52+i*156; box(cx,396,132,40,C['bg'],C['stroke'],1,20,False); t(cx+66,421,x,14.5,600,None,'middle')
        if i<len(chips)-1: o.append(f'<path d="M{cx+136},416 L{cx+152},416" fill="none" stroke="{C["line"]}" stroke-width="1.8" marker-end="url(#m)"/>')
    t(852,411,'换掉任何一个会话（包括舰长），',14.5,500); t(852,435,'项目照样接得上。',14.5,500)
    if LANG!='zh':
        path=os.path.join(os.path.dirname(path),LANG,os.path.basename(path))
    if THEME=='dark':
        path=os.path.join(os.path.dirname(path),'dark',os.path.basename(path))
    os.makedirs(os.path.dirname(path),exist_ok=True)
    head=(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {HW} {HH}" width="{HW}" height="{HH}" font-family="-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Hiragino Sans GB\',\'Noto Sans CJK SC\',\'Helvetica Neue\',Arial,sans-serif">'
          '<defs><filter id="sh" x="-5%" y="-5%" width="110%" height="120%"><feDropShadow dx="0" dy="1" stdDeviation="1.5" flood-color="#0F172A" flood-opacity="0.08"/></filter>'
          f'<marker id="m" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{C["line"]}"/></marker></defs>'
          f'<rect x="0.5" y="0.5" width="{HW-1}" height="{HH-1}" rx="16" fill="{C["canvas"]}" stroke="{C["stroke"]}"/>')
    open(path,'w',encoding='utf-8').write(head+''.join(o)+'</svg>')
hero('docs/img/hero.svg')
print('svg x7')
