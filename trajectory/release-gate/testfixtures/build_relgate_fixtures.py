#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 release-gate/testfixtures/relgate/ 下的 14 个准入判据验证 fixture。

背景：第 6 条纪律（图接线前必须在病态 fixture 上跑出全部报警分支 + 干净样本反验不误
报）在 make_worksheet.py 的 A1-A5 准入判据上一期欠了账——A2/A5 从未在受控样本上验过
两态，A4 只在一个真实历史提交上验过"能命中"没验过"不误伤正确写法"。本脚本把受控
样本一次性造齐，配合 make_worksheet.py 新增的 --registry-json 离线登记模式（见
PIPELINE-SPEC.md §A0），让验证过程完全不碰共享库 ${DB_NAME}。

<日期> 补齐 A3（premerge-gate 四查 g1-g4）的 8 个样本：首批 6 个覆盖了
A1/A2/A4/A5，**A3 一项都没有**，而它的四查在生成器里是与图并存的第二套实现。四查
每项各配一个"该报"与一个"不该报"的样本，另加一个 cherry-pick 冲突的真实组合态。

每个 fixture 是一个独立 git 仓库（`<fixture>/repo/`），都以 `feat/<项目>-integration`
分支为基线（A3 的门禁检查需要这个分支名，make_worksheet.py 里硬编码 INTEGRATION_BRANCH
常量取的就是这个名字），旁边配一份 `registry.json`（离线登记数据，格式见 PIPELINE-SPEC.md
§A0），F-CLEAN 额外配一份 `receipts/CS-FIXTURE-CLEAN.md`（离线回执样本，供 A1 PASS 分支
验证用）。

只用 Python 3 标准库（subprocess 跑只读+建仓 git 命令、pathlib、json、shutil）。

用法：
    python3 build_relgate_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

FIXTURES_ROOT = Path(__file__).resolve().parent / "relgate"
INTEGRATION_BRANCH = "feat/<项目>-integration"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git -C {repo} {' '.join(args)} 失败: {r.stderr.strip()}")
    return r


def init_repo(rel_name: str) -> Path:
    """rel_name 形如 'F-A4a/repo'，返回创建好的仓库绝对路径。"""
    repo = FIXTURES_ROOT / rel_name
    fixture_dir = repo.parent
    if fixture_dir.exists():
        shutil.rmtree(fixture_dir)
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", INTEGRATION_BRANCH)
    # 仅仓库局部 config（不碰全局），造仓库跑 commit 的必需前提，不是绕过什么纪律。
    git(repo, "config", "user.email", "fixture@relgate.local")
    git(repo, "config", "user.name", "relgate-fixture-builder")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


def write_file(repo: Path, rel_path: str, content: str) -> None:
    """显式 newline='' 写盘，避免任何平台相关的行尾翻译——A3.g4 行尾判据对 CR 字节
    敏感，fixture 内容必须逐字是我们写下的样子（<日期> 用户既有教训：批量写盘不带
    newline='' 会把行尾静默翻掉）。"""
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        f.write(content)


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def checkout_new_branch(repo: Path, branch: str) -> None:
    git(repo, "checkout", "-q", "-b", branch)


def diff_name_only(repo: Path, base: str, head: str) -> list[str]:
    r = git(repo, "diff", "--name-only", base, head)
    return [l for l in r.stdout.splitlines() if l.strip()]


def write_registry(
    fixture_dir: Path,
    *,
    changeset_no: str,
    commit_hash: str,
    files: list[str],
    title: str,
    receipt_dir: str | None = None,
) -> None:
    registry: dict[str, Any] = {
        "changeset": {
            "id": "9001",
            "changeset_no": changeset_no,
            "title": title,
            "change_type": "feature",
            "commit_hash": commit_hash,
            "file_count": str(len(files)),
            "status": "1",
            "operator_name": "relgate-fixture-builder",
            "reviewer_name": "",
            "remark": "release-gate fixture，仅供 make_worksheet.py --registry-json 离线验证用，非真实登记",
        },
        "change_files": files,
    }
    if receipt_dir:
        registry["receipt_dir"] = receipt_dir
    (fixture_dir / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Widget.java 三态内容（无新字段 / 新字段无出口 / 新字段带 exist=false）
# ---------------------------------------------------------------------------

WIDGET_BASE = """package com.<基座项目>.domain.entity;

public class Widget {
    private Long id;
    private String name;
}
"""

WIDGET_A4_FAIL = """package com.<基座项目>.domain.entity;

public class Widget {
    private Long id;
    private String name;
    private String extraLabel;
}
"""

WIDGET_A4_PASS = """package com.<基座项目>.domain.entity;

public class Widget {
    private Long id;
    private String name;

    @TableField(exist = false)
    private String extraLabel;
}
"""


def build_f_a4a() -> None:
    """A4 FAIL：新增 private 字段，无对应 DDL、无 @TableField(exist=false)——
    <事故编号> 同款雷。"""
    repo = init_repo("F-A4a/repo")
    write_file(repo, "src/main/java/com/<基座项目>/domain/entity/Widget.java", WIDGET_BASE)
    base_sha = commit_all(repo, "baseline: Widget 实体")
    checkout_new_branch(repo, "feature/f-a4a")
    write_file(repo, "src/main/java/com/<基座项目>/domain/entity/Widget.java", WIDGET_A4_FAIL)
    head_sha = commit_all(repo, "feat: Widget 新增 extraLabel 字段（故意漏 DDL/exist=false，验 A4 FAIL）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A4A", commit_hash=head_sha, files=files,
        title="fixture F-A4a：实体新增字段无出口，验 A4 FAIL",
    )


def build_f_a4b() -> None:
    """A4 PASS：同样新增 private 字段，但带 @TableField(exist=false)——验不误伤正确写法。"""
    repo = init_repo("F-A4b/repo")
    write_file(repo, "src/main/java/com/<基座项目>/domain/entity/Widget.java", WIDGET_BASE)
    base_sha = commit_all(repo, "baseline: Widget 实体")
    checkout_new_branch(repo, "feature/f-a4b")
    write_file(repo, "src/main/java/com/<基座项目>/domain/entity/Widget.java", WIDGET_A4_PASS)
    head_sha = commit_all(repo, "feat: Widget 新增 extraLabel 派生字段（带 @TableField(exist=false)，验 A4 PASS）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A4B", commit_hash=head_sha, files=files,
        title="fixture F-A4b：实体新增字段带 exist=false，验 A4 不误伤",
    )


def build_f_a5() -> None:
    """A5 NEEDS_HUMAN：改动含 db/migrations/*.sql。"""
    repo = init_repo("F-A5/repo")
    write_file(repo, "README.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a5")
    write_file(
        repo,
        "db/migrations/20260807_add_widget_extra_label.sql",
        "ALTER TABLE t_widget ADD COLUMN extra_label VARCHAR(64) NULL;\n",
    )
    head_sha = commit_all(repo, "feat: 新增 db 迁移文件（验 A5 NEEDS_HUMAN 触发）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A5", commit_hash=head_sha, files=files,
        title="fixture F-A5：含 DB 迁移文件，验 A5 NEEDS_HUMAN",
    )


def build_f_a2a() -> None:
    """A2 FAIL：registry.json 的文件明细比实际 diff 多一个从未改动过的文件。"""
    repo = init_repo("F-A2a/repo")
    write_file(repo, "docs/a.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a2a")
    write_file(repo, "docs/b.md", "candidate change\n")
    head_sha = commit_all(repo, "feat: 改动 docs/b.md（registry 清单故意多列一个文件，验 A2 FAIL）")
    real_files = diff_name_only(repo, base_sha, head_sha)
    registry_files = real_files + ["docs/ghost-never-touched.md"]
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A2A", commit_hash=head_sha, files=registry_files,
        title="fixture F-A2a：登记清单比实际改动多一个文件，验 A2 FAIL",
    )


def build_f_a2b() -> None:
    """A2 PASS：三个提交的特性分支，registry 清单对应最早基线（HEAD~3）。"""
    repo = init_repo("F-A2b/repo")
    write_file(repo, "docs/base.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a2b")
    write_file(repo, "docs/f1.md", "f1\n")
    commit_all(repo, "feat: commit 1/3")
    write_file(repo, "docs/f2.md", "f2\n")
    commit_all(repo, "feat: commit 2/3")
    write_file(repo, "docs/f3.md", "f3\n")
    head_sha = commit_all(repo, "feat: commit 3/3（验 A2 多提交基线搜索命中 N=3）")
    cumulative_files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A2B", commit_hash=head_sha, files=cumulative_files,
        title="fixture F-A2b：三提交特性分支，登记清单对应 HEAD~3，验 A2 PASS 多提交基线搜索",
    )


# ---------------------------------------------------------------------------
# A3（premerge-gate.v6 四查 g1-g4）的负例与"不误伤"样本
#
# 欠账来源：既有 6 个 fixture 覆盖了 A1/A2/A4/A5，**A3 一个都没有**。而 A3 的四查在
# make_worksheet.py 里是**第二套独立实现**（图 premerge-gate.v6 里还有一套，由 runner
# 执行）。此前只有图那套在 fixture 上验过，生成器这套的"与图逐字一致"是注释里的声明、
# 不是验过的事实。按硬约定 5/6，零反例的判决点不许上线——这批就是来还这笔账的。
#
# 每个 fixture 只让**一个**判决点变红（G2b 除外，它刻意造真实的三红组合态），
# 其余三项必须保持 PASS：这样某项 FAIL 才能归因到该项本身，而不是被别的红掩盖。
# 按硬约定 9，四项里每一项都要配一个"不该报"的反向样本。
# ---------------------------------------------------------------------------


def git_allow_fail(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """给预期会非零退出的 git 命令用（如故意制造冲突的 cherry-pick）。"""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _a3_baseline_and_feature(repo: Path, branch: str) -> tuple[str, str, list[str]]:
    """A3 系列共用的最小骨架：基线一提交 + 特性分支一提交，返回 (base, head, files)。

    A3 四查全部只看工作区/git-dir/全仓内容，与改动内容本身无关，所以骨架尽量朴素——
    改动内容越无辜，某项变红就越能归因到 fixture 刻意造的那个病灶上。"""
    write_file(repo, "docs/notes.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, branch)
    write_file(repo, "docs/notes.md", "baseline\nfeature line\n")
    head_sha = commit_all(repo, "docs: 无辜的一行改动（A3 系列骨架）")
    return base_sha, head_sha, diff_name_only(repo, base_sha, head_sha)


def build_f_a3_g1a() -> None:
    """g1 FAIL：已跟踪文件有未提交改动（脏树）。

    提交完成后再改一次且不提交——登记明细对应的是两个提交之间的 diff，不受工作区脏
    影响，所以 A1/A2 仍然 PASS，只有 g1 变红。"""
    repo = init_repo("F-A3-G1a/repo")
    base_sha, head_sha, files = _a3_baseline_and_feature(repo, "feature/f-a3-g1a")
    write_file(repo, "docs/notes.md", "baseline\nfeature line\n提交之后又改的一行，故意不提交\n")
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G1A", commit_hash=head_sha, files=files,
        title="fixture F-A3-G1a：已跟踪文件有未提交改动，验 g1 FAIL",
    )


def build_f_a3_g1b() -> None:
    """g1 应 PASS（不误伤）：工作区只有未跟踪文件（porcelain 的 '??' 行）。

    判据明确把 '??' 排除在外——未跟踪文件不会被并进去，不构成阻断理由。这个样本验的是
    "不该报的不报"：若哪天有人把 '??' 也算作脏，正常带临时文件的 worktree 会被全拦。"""
    repo = init_repo("F-A3-G1b/repo")
    base_sha, head_sha, files = _a3_baseline_and_feature(repo, "feature/f-a3-g1b")
    write_file(repo, "scratch/debug.log", "本地调试产物，未跟踪\n")
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G1B", commit_hash=head_sha, files=files,
        title="fixture F-A3-G1b：只有未跟踪文件，验 g1 不误伤",
    )


def build_f_a3_g2a() -> None:
    """g2 FAIL（隔离）：bisect 会话进行中。

    选 bisect 而非 merge/cherry-pick，是因为实测 `git bisect start` **不移动 HEAD、
    工作区保持干净**，于是 g1/g3/g4 全绿、只有 g2 变红——这是四种中态里唯一能做到
    完全隔离的一种。git-dir 下留下的标记是 BISECT_LOG。"""
    repo = init_repo("F-A3-G2a/repo")
    base_sha, head_sha, files = _a3_baseline_and_feature(repo, "feature/f-a3-g2a")
    git(repo, "bisect", "start")
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G2A", commit_hash=head_sha, files=files,
        title="fixture F-A3-G2a：bisect 中态、树干净，验 g2 隔离 FAIL",
    )


def build_f_a3_g2b() -> None:
    """真实组合态：cherry-pick 撞冲突停在半路 → g1+g2+g3 同时 FAIL。

    这是现场最常见的中断形态，三项一起红才是它的真实样子。隔离样本证明每项**能**单独
    红，这个样本证明它们在真实事故形态下**确实都**红——两者缺一不可。"""
    repo = init_repo("F-A3-G2b/repo")
    write_file(repo, "src/app.txt", "共同祖先\n")
    base_sha = commit_all(repo, "baseline")

    checkout_new_branch(repo, "other/side")
    write_file(repo, "src/app.txt", "旁支改法\n")
    side_sha = commit_all(repo, "other: 旁支改同一行")

    git(repo, "checkout", "-q", INTEGRATION_BRANCH)
    checkout_new_branch(repo, "feature/f-a3-g2b")
    write_file(repo, "src/app.txt", "候选改法\n")
    head_sha = commit_all(repo, "feat: 候选改同一行")
    files = diff_name_only(repo, base_sha, head_sha)

    r = git_allow_fail(repo, "cherry-pick", side_sha)
    if r.returncode == 0:
        raise RuntimeError("F-A3-G2b 预期 cherry-pick 撞冲突，实际却成功了——fixture 没造出目标状态")

    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G2B", commit_hash=head_sha, files=files,
        title="fixture F-A3-G2b：cherry-pick 冲突停在半路，验 g1+g2+g3 真实组合 FAIL",
    )


def build_f_a3_g3a() -> None:
    """g3 FAIL：把 git 真实写出来的冲突标记块提交进仓库（树干净、无中态，隔离验 g3）。

    注意标记块要按 git 真实写法带标签（`<<<<<<< HEAD` / `>>>>>>> other`），不要写成
    光秃秃的七个尖括号——判据的正则要求整行**恰好等于**那七个字符，带标签的行匹配不上。
    用真实写法造样本，才能验出判据在现场到底靠哪一行生效。"""
    repo = init_repo("F-A3-G3a/repo")
    write_file(repo, "docs/notes.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a3-g3a")
    write_file(
        repo,
        "src/merged.txt",
        "前文\n<<<<<<< HEAD\n我方改法\n=======\n对方改法\n>>>>>>> other/side\n后文\n",
    )
    head_sha = commit_all(repo, "feat: 误把未解决的冲突块提交进来（验 g3 FAIL）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G3A", commit_hash=head_sha, files=files,
        title="fixture F-A3-G3a：提交进真实冲突标记块，验 g3 FAIL",
    )


def build_f_a3_g3b() -> None:
    """g3 应 PASS（不误伤）：markdown 的 setext 一级标题下划线恰好七个 '='。

    风险来源：判据正则的三个分支里，`<<<<<<<` 和 `>>>>>>>` 在现场都带标签后缀、匹配不
    上，真正生效的只有光秃秃的 `=======` 那一行；而 markdown 的一级标题下划线正是一串
    '='，写成恰好七个就与判据完全重合。一份正常文档会不会被报成"冲突未解决"，此前从未
    有人验过。造出来跑，不靠推断。"""
    repo = init_repo("F-A3-G3b/repo")
    write_file(repo, "docs/notes.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a3-g3b")
    write_file(repo, "docs/guide.md", "使用说明\n=======\n正文第一段。\n")
    head_sha = commit_all(repo, "docs: setext 标题下划线恰好七个等号（验 g3 不误伤）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G3B", commit_hash=head_sha, files=files,
        title="fixture F-A3-G3b：markdown setext 标题下划线，验 g3 是否误伤",
    )


def build_f_a3_g4a() -> None:
    """g4 FAIL：本次**新增**一个整篇 CRLF 的文件（项目基线是 LF）。

    新增文件走的是判据里 status=A 的分支：base 侧无内容可比，规则就是"含 CR 即 FAIL"。"""
    repo = init_repo("F-A3-G4a/repo")
    write_file(repo, "docs/notes.md", "baseline\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-a3-g4a")
    write_file(repo, "scripts/deploy.bat", "@echo off\r\necho deploying\r\nexit /b 0\r\n")
    head_sha = commit_all(repo, "feat: 新增整篇 CRLF 文件（验 g4 FAIL）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G4A", commit_hash=head_sha, files=files,
        title="fixture F-A3-G4a：新增整篇 CRLF 文件，验 g4 FAIL",
    )


def build_f_a3_g4b() -> None:
    """g4 应 PASS（不误伤）：**存量**就是 CRLF 的文件，本次只改内容、行尾一根没动。

    这正是硬约定 9 点名要造的"存量带病、本次无辜"场景，也是两笔并线阻断级误报的原型：
    旧判据看"改后有没有 CR"这个绝对状态，会把这种正常改动一律拦掉。增量判据的算式是
    改后CR == 改前CR + 新增行CRLF - 删除行CRLF；本样本刻意让三个数都非零，避免出现
    "全是 0 所以恰好相等"的假通过。"""
    repo = init_repo("F-A3-G4b/repo")
    write_file(repo, "docs/notes.md", "baseline\n")
    # 存量 CRLF 文件：5 行，改前 CR=5
    write_file(repo, "legacy/win.ini", "[main]\r\nname=alpha\r\nmode=1\r\nretry=3\r\n[end]\r\n")
    base_sha = commit_all(repo, "baseline（含一个存量 CRLF 文件）")
    checkout_new_branch(repo, "feature/f-a3-g4b")
    # 改 1 行 + 增 1 行，行尾照旧 CRLF：改后 CR=6，新增行CRLF=2，删除行CRLF=1，5+2-1=6 → PASS
    write_file(repo, "legacy/win.ini", "[main]\r\nname=alpha\r\nmode=2\r\nretry=3\r\ntimeout=30\r\n[end]\r\n")
    head_sha = commit_all(repo, "chore: 存量 CRLF 文件改两行，行尾原样保留（验 g4 不误伤）")
    files = diff_name_only(repo, base_sha, head_sha)
    write_registry(
        repo.parent, changeset_no="CS-FIXTURE-A3G4B", commit_hash=head_sha, files=files,
        title="fixture F-A3-G4b：存量 CRLF 文件正常改动，验 g4 不误伤",
    )


def build_f_clean() -> None:
    """干净候选：单提交、清单一致、无实体改动、无迁移 → 准入五项全 PASS。
    防止判据退化成"逢查必拦"。"""
    repo = init_repo("F-CLEAN/repo")
    fixture_dir = repo.parent
    write_file(repo, "docs/notes.md", "baseline notes\n")
    base_sha = commit_all(repo, "baseline")
    checkout_new_branch(repo, "feature/f-clean")
    write_file(repo, "docs/notes.md", "baseline notes\nupdated once, nothing scary here\n")
    head_sha = commit_all(repo, "docs: 更新说明文档（干净候选，无实体/无迁移，验证准入五项全 PASS）")
    files = diff_name_only(repo, base_sha, head_sha)

    receipts_dir = fixture_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_text = (
        "# 回执 · CS-FIXTURE-CLEAN\n\n"
        "- **状态**：PASS（release-gate fixture，干净候选，供 A1-A5 全 PASS 分支验证用，非真实变更）\n\n"
        f"verified_by=校验官 / verdict=PASS / anchor={head_sha} / ts=<日期>T00:00:00Z\n"
    )
    with (receipts_dir / "CS-FIXTURE-CLEAN.md").open("w", encoding="utf-8", newline="") as f:
        f.write(receipt_text)

    write_registry(
        fixture_dir, changeset_no="CS-FIXTURE-CLEAN", commit_hash=head_sha, files=files,
        title="fixture F-CLEAN：干净候选，准入五项全 PASS",
        receipt_dir="receipts",
    )


def main() -> None:
    FIXTURES_ROOT.mkdir(parents=True, exist_ok=True)
    builders = [
        ("F-A4a", build_f_a4a),
        ("F-A4b", build_f_a4b),
        ("F-A5", build_f_a5),
        ("F-A2a", build_f_a2a),
        ("F-A2b", build_f_a2b),
        ("F-A3-G1a", build_f_a3_g1a),
        ("F-A3-G1b", build_f_a3_g1b),
        ("F-A3-G2a", build_f_a3_g2a),
        ("F-A3-G2b", build_f_a3_g2b),
        ("F-A3-G3a", build_f_a3_g3a),
        ("F-A3-G3b", build_f_a3_g3b),
        ("F-A3-G4a", build_f_a3_g4a),
        ("F-A3-G4b", build_f_a3_g4b),
        ("F-CLEAN", build_f_clean),
    ]
    for name, fn in builders:
        fn()
        print(f"[OK] {name} -> {FIXTURES_ROOT / name}")


if __name__ == "__main__":
    main()
