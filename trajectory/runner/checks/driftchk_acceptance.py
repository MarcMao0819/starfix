#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRIFTCHK p6/p7 sandbox acceptance; no production ledger, alert, cursor, or MySQL writes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
POLLER = ROOT / "runner" / "audit-poller.sh"
PROD_DECISIONS = Path("${TRAJ_DATA_DIR}/changeset-audit/机检判决.jsonl")


def run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), env=env, text=True, capture_output=True)


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], repo)
    if result.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def make_integration_fixture(root: Path) -> tuple[Path, str, str]:
    repo = root / "integration"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "driftchk@example.invalid")
    git(repo, "config", "user.name", "DRIFTCHK")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "merge(波35): base")
    live = git(repo, "rev-parse", "HEAD")
    waves = [
        ("frontend/src/api/customer-summary.ts", "export const a = 1;\n", "merge(波36): api"),
        ("src/main/java/com/example/controller/CustomerController.java", "class CustomerController {}\n", "merge(波37): controller"),
        ("db/migrations/20260904_fixture.sql", "SELECT 1;\n", "merge(波38): migration"),
        ("frontend/src/api/customer-route.ts", "export const b = 2;\n", "merge(波39): api"),
        ("README.md", "head\n", "merge(波40): release"),
    ]
    for rel, body, subject in waves:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        git(repo, "add", rel)
        git(repo, "commit", "-qm", subject)
    return repo, live, git(repo, "rev-parse", "HEAD")


def make_docker_stub(root: Path) -> Path:
    stub = root / "docker-stub.sh"
    stub.write_text("""#!/bin/sh
args="$*"
case "$args" in
  *"SELECT changeset_no FROM t_code_changeset"*) exit 0 ;;
  *"SELECT changeset_no, DATE_FORMAT(update_time"*) exit 0 ;;
  *"SELECT DATE_FORMAT(NOW()"*) printf '<日期> 13:47:00\\n' ;;
  *"SHOW TABLES LIKE"*) printf 't_runtime_error_log\\nt_request_anomaly_log\\n' ;;
  *"MAX(id)"*"t_runtime_error_log"*) printf '11\\n' ;;
  *"MAX(id)"*"t_request_anomaly_log"*) printf '0\\n' ;;
  *"FROM t_runtime_error_log"*) printf 'runtime\\t11\\t/api/sales-order/customer-summary\\n' ;;
  *"FROM t_request_anomaly_log"*) exit 0 ;;
  *) printf 'unexpected docker-stub query: %s\\n' "$args" >&2; exit 9 ;;
esac
""", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def make_empty_drift_stub(root: Path) -> Path:
    stub = root / "drift-stub.py"
    stub.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def sandbox_env(root: Path, integration: Path, live_json: Path, docker_stub: Path, drift_stub: Path) -> dict[str, str]:
    pilot = root / "pilot"
    alerts = root / "alerts"
    pilot.mkdir()
    alerts.mkdir()
    for name in ("判决指纹.jsonl", "机检判决.jsonl", "A门判定.jsonl", ".baseline_exempt"):
        (pilot / name).write_text("", encoding="utf-8")
    (pilot / ".audit-poller.cursor").write_text("CS-99999999-9999\n", encoding="utf-8")
    runtime_db = root / "runtime.db"
    sqlite3.connect(runtime_db).close()  # No standard changeset enters this sidecar-only sandbox.
    env = dict(os.environ)
    env.update({
        "AUDITPOLLER_ROOT": str(ROOT), "AUDITPOLLER_PILOT": str(pilot),
        "AUDITPOLLER_ALERT_DIR": str(alerts), "AUDITPOLLER_WORKDIR": str(root / "workdir"),
        "AUDITPOLLER_RUNTIME_DB": str(runtime_db),
        "AUDITPOLLER_CURSOR": str(pilot / ".audit-poller.cursor"),
        "AUDITPOLLER_HEARTBEAT": str(pilot / ".audit-poller.heartbeat"),
        "AUDITPOLLER_FINGERPRINT": str(pilot / "判决指纹.jsonl"),
        "AUDITPOLLER_BASELINE_EXEMPT": str(pilot / ".baseline_exempt"),
        "AUDITPOLLER_DECISIONS": str(pilot / "机检判决.jsonl"),
        "AUDITPOLLER_GATE_LEDGER": str(pilot / "A门判定.jsonl"),
        "AUDITPOLLER_ERROR_LEDGER": str(pilot / "runner.err.jsonl"),
        "AUDITPOLLER_LOCK_FILE": str(pilot / "poller.lock"),
        "AUDITPOLLER_SUPERSEDE_LEDGER": str(pilot / "supersede.jsonl"),
        "AUDITPOLLER_UPDATE_WATERMARK": str(pilot / "update-watermark"),
        "AUDITPOLLER_UPDATE_DONE": str(pilot / "update-done.jsonl"),
        "AUDITPOLLER_LIVE_JSON": str(live_json),
        "AUDITPOLLER_INTEGRATION_REPO": str(integration),
        "AUDITPOLLER_DEPLOY_DRIFT_STATE": str(pilot / "deploy-drift-state.json"),
        "AUDITPOLLER_ROUTE404_CURSOR": str(pilot / "route404-cursor.json"),
        "AUDITPOLLER_DOCKER_BIN": str(docker_stub), "AUDITPOLLER_DRIFT_SCAN": str(drift_stub),
        "TRJ_PILOT_DIR": str(pilot), "TRJ_LEDGER": str(pilot / "判决指纹.jsonl"),
        "TRJ_INVALID": str(pilot / "invalid.jsonl"),
    })
    return env


def decisions(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    production_before = sha256(PROD_DECISIONS)
    graph_before = sha256(ROOT / "runner" / "run_graph.py")
    sandbox = Path(tempfile.mkdtemp(prefix="driftchk-acceptance-"))
    try:
        integration, live, head = make_integration_fixture(sandbox)
        live_json = sandbox / "LIVE.json"
        live_json.write_text(json.dumps({"commit": live}), encoding="utf-8")
        env = sandbox_env(sandbox, integration, live_json, make_docker_stub(sandbox), make_empty_drift_stub(sandbox))

        first = run(["bash", str(POLLER), "--once"], ROOT, env)
        if first.returncode:
            raise RuntimeError("first poller run failed: %s" % (first.stdout + first.stderr))
        if "ALERT_DEPLOY_DRIFT" not in first.stdout or "waves=36,37,38,39,40" not in first.stdout:
            raise RuntimeError("p6 positive alert missing: %s" % first.stdout)
        if "ALERT_ROUTE_404 /api/sales-order/customer-summary 1" not in first.stdout:
            raise RuntimeError("p7 positive alert missing: %s" % first.stdout)
        pilot = sandbox / "pilot"
        rows1 = decisions(pilot / "机检判决.jsonl")
        if sum(row.get("overall") == "ALERT_DEPLOY_DRIFT" for row in rows1) != 1:
            raise RuntimeError("p6 did not write exactly one first-head decision")
        if sum(row.get("overall") == "ALERT_ROUTE_404" for row in rows1) != 1:
            raise RuntimeError("p7 did not write exactly one first-cursor decision")

        second = run(["bash", str(POLLER), "--once"], ROOT, env)
        if second.returncode or second.stdout.strip():
            raise RuntimeError("same HEAD/cursor must be silent: %s" % (second.stdout + second.stderr))
        if len(decisions(pilot / "机检判决.jsonl")) != len(rows1):
            raise RuntimeError("same HEAD/cursor duplicated a decision")

        live_json.write_text(json.dumps({"commit": head}), encoding="utf-8")
        third = run(["bash", str(POLLER), "--once"], ROOT, env)
        if third.returncode:
            raise RuntimeError("catch-up run failed: %s" % (third.stdout + third.stderr))
        if (pilot / "deploy-drift-state.json").exists():
            raise RuntimeError("p6 state did not self-clear after LIVE caught up")
        alerts = list((sandbox / "alerts").glob("机检告警-DEPLOY-DRIFT-*.md"))
        if len(alerts) != 1 or "## 自动清除" not in alerts[0].read_text(encoding="utf-8"):
            raise RuntimeError("p6 alert did not retain an automatic-clear trace")
        cursor = json.loads((pilot / "route404-cursor.json").read_text(encoding="utf-8"))
        if cursor.get("t_runtime_error_log") != 11 or cursor.get("t_request_anomaly_log") != 0:
            raise RuntimeError("p7 cursor did not persist table watermarks")
        if sha256(ROOT / "runner" / "run_graph.py") != graph_before:
            raise RuntimeError("p1-p5 runner source changed during acceptance")
        if sha256(PROD_DECISIONS) != production_before:
            raise RuntimeError("acceptance touched production 机检判决.jsonl")
        print("DRIFTCHK acceptance PASS: p6 alert/dedupe/clear + p7 cursor/route alert + p1-p5 source unchanged")
        return 0
    finally:
        shutil.rmtree(sandbox)


if __name__ == "__main__":
    raise SystemExit(main())
