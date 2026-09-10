#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务落盘激活器（Owner 提议）
用法：
  task-activator.py add <ID> "<标题>" [--owner 窗口] [--book 路径] [--receipt 路径] [--blocker 文本] [--status 待开工|施工中|已完成]
  task-activator.py set <ID> <待开工|施工中|已完成> [--owner 窗口] [--note 文本] [--blocker 文本|-]
  task-activator.py list
  task-activator.py report        # 给舰长的状态汇报（Monitor 每小时调用）
规则：每个任务只有三态；回执文件出现终态（PASS/FAIL/BLOCKED/PARTIAL/请示）而登记仍为施工中 → 汇报里标「回执已到待处置」。
"""
import json, os, sys, re, datetime, argparse
# 机器舰员代号前缀（人类任务不匹配）；按你的命名改环境变量 FLEET_MACHINE_OWNER_RE
MACHINE_OWNER_RE = os.environ.get('FLEET_MACHINE_OWNER_RE', r'^(crew-|bot-|claude-)')
# 舰队工作目录：必须由环境变量给。没有默认值——默认值只会让脚本在别人机器上
# 安静地读写错地方（见 doctrine/02 关于「有缺省的配置才危险」那条）。
BASE = os.environ.get('FLEET_HOME')
if not BASE:
    raise SystemExit('缺少环境变量 FLEET_HOME（舰队工作目录）。见 scripts/README-env.md')
# 子路径各自可覆盖，默认落在 $FLEET_HOME 下
ASK_PANEL = os.environ.get('FLEET_ASK_PANEL', os.path.join(BASE, '请示台.md'))
DISPATCH_LOG = os.environ.get('FLEET_DISPATCH_LOG', os.path.join(BASE, 'dispatch.log'))
WATCHDOG_TXT = os.environ.get('FLEET_WATCHDOG_TXT', os.path.join(BASE, 'watchdog.txt'))
INTEGRATION_REPO = os.environ.get('FLEET_INTEGRATION_REPO', '')
# 测试/复核可用 ACTIVATOR_JSON 指向副本；未传时保持正式激活器路径不变。
DB=os.environ.get('ACTIVATOR_JSON', os.path.join(BASE, 'task-activator.json'))
STATES=('待开工','施工中','已完成')
TERM=re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*+]\s*)?(?:[★☆]\s*)?(?:当前)?(?:状态|终态|结论)\s*[：:]\s*[`*_\s]*(PASS|FAIL|BLOCKED|PARTIAL|DONE|HANDOFF|IN_PROGRESS|请示|待裁定|完成|部分完成|取消)")
def now(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
def load():
    if not os.path.exists(DB): return {'tasks':[]}
    return json.load(open(DB,encoding='utf-8'))
def save(d): json.dump(d,open(DB,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
def find(d,tid):
    for t in d['tasks']:
        if t['id']==tid: return t
    return None
def receipt_state(path):
    if not path or not os.path.exists(path): return None
    try: txt=open(path,encoding='utf-8',errors='ignore').read()
    except Exception: return None
    m=TERM.search(txt); return m.group(1) if m else None
def review_path(receipt):
    if not receipt: return ''
    base=receipt[:-3] if receipt.endswith('.md') else receipt
    return base+'-REVIEW.md'
def acted_after_receipt(t):
    """舰长在回执落盘之后 set 过（updated 晚于回执 mtime）即视为已处置"""
    try:
        import os, datetime as _d
        paths=[x for x in (t.get('receipt'), review_path(t.get('receipt'))) if x and os.path.exists(x)]
        if not paths: return False
        m=max(os.path.getmtime(x) for x in paths)
        u=_d.datetime.strptime(t['updated'],'%Y-%m-%d %H:%M').timestamp()
        return u>=m
    except Exception: return False
def phase_of(t):
    """施工中的相位：承建中 / 回执已到(X) / 复审中 / 复审PASS待并线 / 复审FAIL待增补"""
    if t['status']!='施工中': return ''
    rs=receipt_state(t['receipt']); rv=receipt_state(review_path(t['receipt']))
    if rv=='PASS': return '复审PASS待并线'
    if rv in ('FAIL','BLOCKED'): return f'复审{rv}待增补'
    if rv: return f'复审中({rv})'
    if rs: return f'回执已到({rs})'
    return '承建中'
CAPTAIN_ALIASES=('舰长','主窗','main','captain','舰长5.1','我','self')
def captain_hook(t, verb):
    """舰长不干活钩子：任务下达（进入施工中）时强制校验并打印铁律。"""
    owner=(t.get('owner') or '').strip()
    if not owner:
        print(f"⛔ 钩子拦截：{t['id']} 没有承建方（--owner），舰长不能把任务留给自己。先指定舰员/窗口再{verb}。"); return False
    if owner.lower() in [x.lower() for x in CAPTAIN_ALIASES]:
        print(f"⛔ 钩子拦截：{t['id']} 的承建方是舰长本人，违反「舰长不自己干活」铁律。改派舰员/窗口。"); return False
    print(f"🪝 舰长钩子｜{t['id']} 已下达给 {owner}。舰长职责只限：任务书、派单、复审派单、验收判定、并线/部署、记档；不得自己写代码、跑验收、代承建交付。")
    with open(DISPATCH_LOG,'a',encoding='utf-8') as f:
        f.write(f"{now()} | {t['id']} | {owner} | {verb}\n")
    return True
# ---------- 决策题（一题映射多任务，答一题解锁多条） ----------
def write_board(d):
    qs=[q for q in d.get('decisions',[]) if not q['answer']]
    qs=sorted(qs,key=lambda q:(-len(q['tasks']),q['asked_at']))
    lines=[f"# 请示台（自动生成 {now()}）","","> 答复只认编号：回「Q03 按此办」「Q06 A」即可。本页由任务激活器每小时重生成，只列待答。",""]
    lines.append(f"待答 {len(qs)} 道 · 合计可解锁 {sum(len(q['tasks']) for q in qs)} 条任务"); lines.append("")
    lines.append("| 编号 | 问谁 | 问题 | 等了 | 解锁 | 关联任务 |"); lines.append("|---|---|---|---|---|---|")
    for q in qs:
        h=hours_since(q['asked_at']); wait=f"{h/24:.1f} 天" if h>=24 else f"{h:.0f} 小时"
        flag='⚠久悬 ' if h>=24 else ''
        lines.append(f"| {q['qid']} | {q['who']} | {flag}{q['question']} | {wait} | {len(q['tasks'])} | {'、'.join(q['tasks'])} |")
    lines.append(""); lines.append("## 每题说明（大白话）"); lines.append("")
    for q in qs:
        lines.append(f"### {q['qid']}　{q['question']}"); lines.append(""); lines.append(q.get('detail') or '（说明待补）'); lines.append("")
        if q.get('recommend'): lines.append(f"**舰长建议：{q['recommend']}**"); lines.append("")
    ans=[q for q in d.get('decisions',[]) if q['answer']]
    if ans:
        lines+=["","## 近期已答（留痕）",""]+[f"- {q['qid']}（{q['answered_at']}）：{q['answer']}" for q in sorted(ans,key=lambda q:q['answered_at'],reverse=True)[:10]]
    open(ASK_PANEL,'w',encoding='utf-8').write('\n'.join(lines)+'\n')
    return len(qs)
def cmd_ask(a):
    d=load(); d.setdefault('decisions',[])
    if a.sub=='add':
        if any(q['qid']==a.qid for q in d['decisions']): print('已存在',a.qid); return
        tasks=[x for x in (a.tasks or '').split(',') if x]
        d['decisions'].append({'qid':a.qid,'question':a.question,'tasks':tasks,'who':a.who or 'Owner','answer':'','asked_at':now(),'answered_at':''})
        for tid in tasks:
            t=find(d,tid)
            if t and t['status']=='待开工' and not t['blocker']: t['blocker']=f'等 {a.who or "Owner"} {a.qid}'
        save(d); print('ask added',a.qid,'→',tasks)
    elif a.sub=='detail':
        q=next((q for q in d['decisions'] if q['qid']==a.qid),None)
        if not q: print('无此决策题',a.qid); sys.exit(1)
        q['detail']=a.question or ''; save(d); print('detail set',a.qid)
    elif a.sub=='recommend':
        q=next((q for q in d['decisions'] if q['qid']==a.qid),None)
        if not q: print('无此决策题',a.qid); sys.exit(1)
        q['recommend']=a.question or ''; save(d); print('recommend set',a.qid)
    elif a.sub=='board':
        n=write_board(load()); print('请示台已生成，待答',n)
    elif a.sub=='answer':
        q=next((q for q in d['decisions'] if q['qid']==a.qid),None)
        if not q: print('无此决策题',a.qid); sys.exit(1)
        q['answer']=a.answer or a.question or ''; q['answered_at']=now(); unlocked=[]
        for tid in q['tasks']:
            t=find(d,tid)
            if not t: continue
            others=[x for x in d['decisions'] if x['qid']!=a.qid and tid in x['tasks'] and not x['answer']]
            if t['status']=='待开工' and not others:
                t['blocker']=''; t['note']=(t.get('note') or '')+f' 【{a.qid} 已答：{a.answer}】'; t['updated']=now(); t['history'].append([now(),'待开工',f'{a.qid} 已答→可开工']); unlocked.append(tid)
            elif t['status']=='待开工':
                t['blocker']='等 '+'、'.join(x['qid'] for x in others); t['updated']=now()
        save(d); print('answered',a.qid,'解锁',unlocked or '无（仍有其他题）')
    else:
        for q in d['decisions']:
            print(f"  {q['qid']} | {q['who']} | {'✅已答：'+q['answer'] if q['answer'] else '⏳待答'} | 解锁 {len(q['tasks'])} 条：{','.join(q['tasks'])}\n     {q['question']}")
def cmd_add(a):
    d=load()
    if find(d,a.id): print('已存在',a.id); return
    if a.worktree and not os.path.isabs(a.worktree):
        print('worktree 必须为绝对路径'); sys.exit(2)
    t={'id':a.id,'title':a.title,'status':a.status,'owner':a.owner or '','book':a.book or '','receipt':a.receipt or '','worktree':a.worktree or '','blocker':a.blocker or '','note':'','created':now(),'updated':now(),'history':[[now(),a.status,'add']]}
    if a.status=='施工中' and not captain_hook(t,'登记为施工中'): sys.exit(2)
    d['tasks'].append(t); save(d); print('added',a.id,a.status)
def completion_check(t,a):
    """反向校验：回执终态 PASS/DONE（或复审 PASS）+（可选）登记号在库 +（可选）并线提交存在；非代码任务用 --evidence；--force 需理由并留痕。"""
    import subprocess
    problems=[]
    if getattr(a,'force',None):
        with open(DISPATCH_LOG,'a',encoding='utf-8') as f: f.write(f"{now()} | {t['id']} | FORCE-完成 | {a.force}\n")
        print(f"⚠ 强制完成（已留痕）：{a.force}"); return True
    if getattr(a,'evidence',None):
        t['note']=(t.get('note') or '')+f' 【完成证据：{a.evidence}】'; return True
    rs=receipt_state(t.get('receipt')); rv=receipt_state(review_path(t.get('receipt')))
    if not t.get('receipt'): problems.append('无回执路径（非代码任务请用 --evidence "…"）')
    elif rv not in ('PASS',) and rs not in ('PASS','DONE'): problems.append(f'回执/复审终态不是 PASS（承建={rs}，复审={rv}）')
    if getattr(a,'reg',None):
        try:
            out=subprocess.run(['/usr/local/bin/docker','exec','${DB_CONTAINER}','sh','-c',f'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N ${DB_NAME} -e "SELECT COUNT(*) FROM t_code_changeset WHERE changeset_no=\'{a.reg}\'"'],capture_output=True,text=True,timeout=30).stdout.strip()
            if out!='1': problems.append(f'登记 {a.reg} 不在库')
        except Exception as e: problems.append(f'登记核验失败：{e}')
    if getattr(a,'merge',None):
        r=subprocess.run(['git','-C',INTEGRATION_REPO,'merge-base','--is-ancestor',a.merge,'HEAD'])
        if r.returncode!=0: problems.append(f'并线提交 {a.merge} 不在集成头祖先链')
    if problems:
        print('⛔ 完成校验未过：'+'；'.join(problems)+'。补证据或 --force "<理由>"。'); return False
    print('✅ 完成校验通过'+(f'（登记 {a.reg}）' if getattr(a,'reg',None) else '')+(f'（并线 {a.merge[:9]}）' if getattr(a,'merge',None) else '')); return True
def cmd_set(a):
    d=load(); t=find(d,a.id)
    if not t: print('无此任务',a.id); sys.exit(1)
    if a.state not in STATES: print('状态只能是',STATES); sys.exit(1)
    worktree_only = (
        a.worktree is not None and a.state == t['status'] and a.owner is None
        and a.note is None and a.blocker is None and not any(
            getattr(a, key, None) for key in ('reg','merge','evidence','force')
        )
    )
    if a.owner is not None: t['owner']=a.owner
    if a.worktree is not None:
        if a.worktree and not os.path.isabs(a.worktree):
            print('worktree 必须为绝对路径'); sys.exit(2)
        t['worktree']=a.worktree
    # 施工中任务补录 worktree 不伪造一次新的业务活动；这样 list 的既有字段保持原样。
    if worktree_only:
        save(d); print('set',a.id,a.state,'worktree')
        return
    if a.state=='施工中' and t['status']!='施工中' and not captain_hook(t,'置为施工中'): sys.exit(2)
    if a.state=='已完成' and t['status']!='已完成' and not completion_check(t,a): sys.exit(3)
    t['status']=a.state; t['updated']=now()
    if a.note is not None: t['note']=a.note
    if a.blocker is not None: t['blocker']='' if a.blocker=='-' else a.blocker
    t['history'].append([now(),a.state,a.note or ''])
    save(d); print('set',a.id,a.state)
def hours_since(s):
    try: return round((datetime.datetime.now()-datetime.datetime.strptime(s,'%Y-%m-%d %H:%M')).total_seconds()/3600,1)
    except Exception: return 0
def cmd_drop(a):
    d=load(); t=find(d,a.id)
    if not t: print('无此任务',a.id); sys.exit(1)
    d.setdefault('dropped',[]).append({**t,'dropped_at':now(),'reason':a.reason or ''}); d['tasks']=[x for x in d['tasks'] if x['id']!=a.id]
    save(d); print('dropped',a.id)
def cmd_list(a):
    d=load()
    for st in STATES:
        rows=[t for t in d['tasks'] if t['status']==st]
        print(f'## {st}（{len(rows)}）')
        for t in rows:
            line=f"  {t['id']} | {t['title']} | {t['owner'] or '-'} | 更新 {t['updated']}"
            line += f" | [{phase_of(t)}]" if st=='施工中' else ''
            line += f" | 卡：{t['blocker']}" if t['blocker'] else ''
            if st=='施工中' and t.get('worktree'):
                line += f" | wt={os.path.basename(os.path.normpath(t['worktree']))}"
            print(line)
def window_sid(owner):
    """从会话清单找 owner（如 crew-a1）对应的终端会话 id"""
    try:
        for ln in open(WATCHDOG_TXT,encoding='utf-8'):
            parts=ln.strip().split(None,1)
            if len(parts)==2 and owner.split('-')[0] in parts[1].split('(')[0]: return parts[0]
    except Exception: pass
    return None
def screen_tail(sid,n=8):
    import subprocess
    scpt='tell application "iTerm2"\nrepeat with w in windows\nrepeat with t in tabs of w\nrepeat with s in sessions of t\nif (unique id of s) is "%s" then return contents of s\nend repeat\nend repeat\nend repeat\nend tell'%sid
    try:
        out=subprocess.run(['osascript','-e',scpt],capture_output=True,text=True,timeout=20).stdout
        lines=[l for l in out.splitlines() if l.strip()]
        return lines[-n:]
    except Exception: return []
def classify_screen(lines):
    txt='\n'.join(lines)
    if 'at capacity' in txt or 'try a different model' in txt.lower(): return '模型容量不足（停摆）'
    if 'Hang tight' in txt or 'thinking a bit more' in txt or 'keep waiting' in txt: return '模型响应慢（等待中，未停摆）'
    if 'Working' in txt or 'esc to interrupt' in txt: return '在干活'
    if 'Ask Codex to do anything' in txt: return '空闲提示符（可能已完工未写回执或在等指令）'
    return '无法判定'
def cmd_stall(a):
    d=load(); th=float(os.environ.get('ACTIVATOR_STALL_H','6'))
    for t in [x for x in d['tasks'] if x['status']=='施工中']:
        h=hours_since(t['updated']); ph=phase_of(t)
        if ph!='承建中' or h<th: continue
        if not re.match(MACHINE_OWNER_RE, (t['owner'] or '')): print(f"⏳ {t['id']}（{t['owner']}，{h}h）真人任务，按飞书追问规则处理"); continue
        sid=window_sid(t['owner'] or '')
        cls=classify_screen(screen_tail(sid)) if sid else '找不到窗口映射'
        act={'模型容量不足（停摆）':'改派或等模型恢复后重投','在干活':'继续等，下轮再看','空闲提示符（可能已完工未写回执或在等指令）':'读回执/催写回执或补指令','无法判定':'人工回读','找不到窗口映射':'核 owner 写法'}.get(cls,'人工回读')
        print(f"⏳ {t['id']}（{t['owner']}，{h}h 承建中）屏幕={cls} → 建议：{act}")
def cmd_report(a):
    d=load(); out=[f"🗂 任务激活器 {now()}"]
    doing=[t for t in d['tasks'] if t['status']=='施工中']
    todo=[t for t in d['tasks'] if t['status']=='待开工']
    done=[t for t in d['tasks'] if t['status']=='已完成']
    ready=[t for t in todo if not t['blocker']]; blocked=[t for t in todo if t['blocker']]
    out.append(f"施工中 {len(doing)} · 待开工 {len(todo)}（可开工 {len(ready)}/卡口径 {len(blocked)}）· 已完成 {len(done)}")
    flags=[]
    for t in doing:
        ph=phase_of(t); h=hours_since(t['updated'])
        tag=''
        if ph.startswith('回执已到') or ph.startswith('复审PASS') or ph.startswith('复审FAIL') or ph.startswith('复审BLOCKED'):
            if acted_after_receipt(t): ph+='·已处置'
            else: tag=' ⚠待舰长处置' + ('（超 1h）' if h>=1 else '')
        elif ph=='承建中' and h>=float(os.environ.get('ACTIVATOR_STALL_H','6')):
            if not re.match(MACHINE_OWNER_RE, (t['owner'] or '')): cls='真人任务'
            else:
                sid=window_sid(t['owner'] or ''); cls=classify_screen(screen_tail(sid)) if sid else '无窗口映射'
            tag=f" ⏳已 {h}h 无终态｜屏幕={cls}"
        out.append(f"  施工中 {t['id']}（{t['owner'] or '-'}，{h}h）[{ph}]{tag}")
        if tag: flags.append(t['id'])
    if ready:
        out.append("  可开工未派："+'、'.join(t['id'] for t in ready)+" ← 有空窗就派")
        qs=[q for q in d.get('decisions',[]) if not q['answer']]
        if qs:
            qs=sorted(qs,key=lambda q:-len(q['tasks']))
            out.append('  待答决策 %d 道；答 %s 可解锁 %d 条'%(len(qs),'、'.join(q['qid'] for q in qs[:3]),sum(len(q['tasks']) for q in qs[:3])))
        stale=[q['qid'] for q in qs if hours_since(q['asked_at'])>=24]
        if stale: out.append('  ⚠久悬（>24h 未答）：'+'、'.join(stale)+' ← 请示台.md 可直接答编号')
    try: write_board(d)
    except Exception as e: out.append(f'  （请示台生成失败：{e}）')
    if blocked:
        groups={}
        for t in blocked:
            b=t['blocker']; k='卡 Owner' if 'Owner' in b else ('卡<质量负责人1>' if '<质量负责人1>' in b or '<质量负责人1>' in b else ('卡<生产负责人1>' if '<生产负责人1>' in b else ('前置未过' if ('先过' in b or '开闸后' in b or '开闸 +' in b) else ('低优先' if '低优先' in b else '卡其他'))))
            groups.setdefault(k,[]).append(t['id'])
        def _short(v): return '、'.join(v) if len(v)<=8 else '、'.join(v[:5])+f'…（共 {len(v)} 条，list 看全）'
        out.append("  卡口径："+'；'.join(f"{k} {len(v)}：{_short(v)}" for k,v in groups.items()))
    today=datetime.date.today().strftime('%Y-%m-%d')
    dt=[t for t in done if t['updated'].startswith(today)]
    if dt: out.append("  今日完成："+'、'.join(t['id'] for t in dt))
    out.append("  🪝 铁律：舰长只派单/复审/验收/并线，不自己干活；可开工的单要派给舰员。")
    print('\n'.join(out))
p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd')
x=sp.add_parser('add'); x.add_argument('id'); x.add_argument('title'); x.add_argument('--owner'); x.add_argument('--book'); x.add_argument('--receipt'); x.add_argument('--worktree'); x.add_argument('--blocker'); x.add_argument('--status',default='待开工',choices=STATES)
x=sp.add_parser('set'); x.add_argument('id'); x.add_argument('state'); x.add_argument('--owner'); x.add_argument('--worktree'); x.add_argument('--note'); x.add_argument('--blocker'); x.add_argument('--reg'); x.add_argument('--merge'); x.add_argument('--evidence'); x.add_argument('--force')
x=sp.add_parser('ask'); x.add_argument('sub',choices=['add','answer','list','board','detail','recommend']); x.add_argument('qid',nargs='?'); x.add_argument('question',nargs='?'); x.add_argument('--tasks'); x.add_argument('--who'); x.add_argument('--answer')
x=sp.add_parser('stall')
x=sp.add_parser('drop'); x.add_argument('id'); x.add_argument('--reason')
sp.add_parser('list'); sp.add_parser('report')
a=p.parse_args(); {'add':cmd_add,'set':cmd_set,'drop':cmd_drop,'list':cmd_list,'report':cmd_report,'ask':cmd_ask,'stall':cmd_stall}.get(a.cmd, lambda _: p.print_help())(a)
