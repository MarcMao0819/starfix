#!/usr/bin/env bash
# AUDITPOLLER (4C03): changeset-audit v5.4 cursor poller.
#
# Production graph contract:
#   input_vars   = changeset_no
#   runtime_vars = workdir
#   --inject     = optional test-only data-drift seam; production must not pass it.
#
# stdout protocol:
#   success:    <changeset_no> <overall>
#   failure:    RUNNER_ERR <changeset_no-or-> <stage>:<reason>
#   idle cycle: no output

set -u
set -o pipefail

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

ROOT=${AUDITPOLLER_ROOT:-${TRAJ_HOME}}
PILOT=${AUDITPOLLER_PILOT:-${TRAJ_DATA_DIR}/changeset-audit}
ALERT_DIR=${AUDITPOLLER_ALERT_DIR:-${FLEET_HOME}/<项目>ERP/迁移备份/回执}
GRAPH=${AUDITPOLLER_GRAPH:-$ROOT/graphs/changeset-audit.v5.4.json}
RUN_GRAPH=${AUDITPOLLER_RUN_GRAPH:-$ROOT/runner/run_graph.py}
REG_GATE=${AUDITPOLLER_REG_GATE:-$ROOT/runner/checks/registration_gate.py}
REG_SETTLE=${AUDITPOLLER_REG_SETTLE:-$ROOT/runner/checks/registration_settle.py}
ALERT_CONSUMER=${AUDITPOLLER_ALERT_CONSUMER:-$ROOT/runner/checks/alert_consumer.py}
DRIFT_SCAN=${AUDITPOLLER_DRIFT_SCAN:-$ROOT/runner/drift_scan.py}
WORKDIR=${AUDITPOLLER_WORKDIR:-$ROOT/workdir}
RUNTIME_DB=${AUDITPOLLER_RUNTIME_DB:-$ROOT/runtime.db}
CURSOR=${AUDITPOLLER_CURSOR:-$PILOT/.audit-poller.cursor}
HEARTBEAT=${AUDITPOLLER_HEARTBEAT:-$PILOT/.audit-poller.heartbeat}
FINGERPRINT=${AUDITPOLLER_FINGERPRINT:-$PILOT/判决指纹.jsonl}
BASELINE_EXEMPT=${AUDITPOLLER_BASELINE_EXEMPT:-$PILOT/.baseline_exempt}
DECISIONS=${AUDITPOLLER_DECISIONS:-$PILOT/机检判决.jsonl}
GATE_LEDGER=${AUDITPOLLER_GATE_LEDGER:-$PILOT/A门判定.jsonl}
ERROR_LEDGER=${AUDITPOLLER_ERROR_LEDGER:-$PILOT/.audit-poller-runner-err.jsonl}
LOCK_FILE=${AUDITPOLLER_LOCK_FILE:-${AUDITPOLLER_LOCK_DIR:-$PILOT/.audit-poller.lock}}
INTERVAL=${AUDITPOLLER_INTERVAL:-60}
RUN_TAG=${AUDITPOLLER_RUN_TAG:-default}
OMIT_REQUIRED_INPUT=${AUDITPOLLER_OMIT_REQUIRED_INPUT:-}
MYSQL_CONTAINER=${AUDITPOLLER_MYSQL_CONTAINER:-${DB_CONTAINER}}
DOCKER_BIN=${AUDITPOLLER_DOCKER_BIN:-/usr/local/bin/docker}
SUPERSEDE_LEDGER=${AUDITPOLLER_SUPERSEDE_LEDGER:-$PILOT/告警撤回留痕.jsonl}
UPDATE_SCAN_STATE=${AUDITPOLLER_UPDATE_WATERMARK:-${AUDITPOLLER_UPDATE_SCAN_STATE:-${AUDITPOLLER_LAST_UPDATE_SCAN:-$PILOT/.last_update_scan}}}
UPDATE_COMPLETED=${AUDITPOLLER_UPDATE_COMPLETED:-${AUDITPOLLER_UPDATE_DONE:-$PILOT/.update-scan-completed.jsonl}}
LIVE_JSON=${AUDITPOLLER_LIVE_JSON:-${FLEET_INTEGRATION_REPO}/.local-instance/LIVE.json}
INTEGRATION_REPO=${AUDITPOLLER_INTEGRATION_REPO:-${FLEET_INTEGRATION_REPO}}
DEPLOY_DRIFT_STATE=${AUDITPOLLER_DEPLOY_DRIFT_STATE:-$PILOT/deploy-drift-state.json}
ROUTE404_CURSOR=${AUDITPOLLER_ROUTE404_CURSOR:-$PILOT/route404-cursor.json}

# 本进程身份，写进心跳供外部判「轮询器重启过没有」。
# `cycle` 是从上一份心跳续上的累计值（init_heartbeat_cycle），**不随重启归零**——
# 这是有意的（要看总拍数），但也意味着**拿 cycle 判重启永远判不出来**：
# <日期> 重启后第一拍就是 11337。所以另给一对随进程走的字段。（非端口：11337 是轮询拍数计数）
POLLER_PID=$$
POLLER_STARTED=$(TZ=Asia/Shanghai date '+%Y-%m-%dT%H:%M:%S%z')

MODE=loop
AUDIT_ONE=
DEFERRED=3
MAX_DEFERRED_CYCLES=${AUDITPOLLER_MAX_DEFERRED_CYCLES:-10}
HEARTBEAT_CYCLE=0
CYCLE_NEW_PICKED=0
CYCLE_DRIFT_PICKED=0
CYCLE_UPDATE_PICKED=0
CYCLE_ERR=
CYCLE_DEFERRED=()
CYCLE_UPDATE_SKIPPED=()
CYCLE_REROUTED=0
DEFERRED_NOS=()
DEFERRED_COUNTS=()
UPDATE_SCAN_WINDOW_END=
UPDATE_SCAN_READY=0
UPDATE_SCAN_FINGERPRINT_BASE=

usage() {
  printf '%s\n' "usage: audit-poller.sh [--once | --audit-one CS-YYYYMMDD-NNNN]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --once)
      MODE=once
      shift
      ;;
    --audit-one)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      MODE=audit_one
      AUDIT_ONE=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [ "$INTERVAL" -lt 1 ]; then
  printf '%s\n' "RUNNER_ERR - config:invalid_interval"
  exit 2
fi
if ! [[ "$MAX_DEFERRED_CYCLES" =~ ^[0-9]+$ ]]; then
  printf '%s\n' "RUNNER_ERR - config:invalid_max_deferred_cycles"
  exit 2
fi
mkdir -p "$WORKDIR" "$ALERT_DIR" "$(dirname "$CURSOR")" \
  "$(dirname "$SUPERSEDE_LEDGER")" "$(dirname "$UPDATE_SCAN_STATE")" \
  "$(dirname "$UPDATE_COMPLETED")" "$(dirname "$DEPLOY_DRIFT_STATE")" \
  "$(dirname "$ROUTE404_CURSOR")"

LOCK_HELD=0

acquire_lock() {
  # shlock uses link(2) for atomic acquisition and automatically replaces a
  # dead-PID lock after reboot/SIGKILL. If an old PID has been reused by an
  # unrelated live process, shlock deliberately blocks and we report
  # RUNNER_ERR for manual resolution; auto-reclaiming that rare case would
  # reintroduce a TOCTOU window between competing reclaimers.
  if /usr/bin/shlock -p "$$" -f "$LOCK_FILE" 2>/dev/null; then
    LOCK_HELD=1
    return 0
  fi
  return 1
}

release_lock() {
  [ "$LOCK_HELD" -eq 1 ] || return 0
  [ ! -e "$LOCK_FILE" ] || unlink "$LOCK_FILE"
  LOCK_HELD=0
}

if ! acquire_lock; then
  printf '%s\n' "RUNNER_ERR - lock:already_running_or_unrecoverable"
  exit 1
fi

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/auditpoller.XXXXXX") || {
  release_lock
  printf '%s\n' "RUNNER_ERR - init:mktemp_failed"
  exit 1
}

cleanup() {
  rm -f "$TMP_ROOT"/* 2>/dev/null || true
  rmdir "$TMP_ROOT" 2>/dev/null || true
  release_lock
}

on_signal() {
  local code=$1
  trap - EXIT INT TERM
  cleanup
  exit "$code"
}

trap cleanup EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

now_iso() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

heartbeat_now_iso() {
  TZ=Asia/Shanghai date '+%Y-%m-%dT%H:%M:%S%z'
}

init_heartbeat_cycle() {
  local prior
  [ -f "$HEARTBEAT" ] || return 0
  if ! prior=$(python3 - "$HEARTBEAT" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
cycle = obj.get("cycle")
if not isinstance(cycle, int) or cycle < 0:
    raise SystemExit(1)
print(cycle)
PY
  ); then
    return 0
  fi
  HEARTBEAT_CYCLE=$prior
}

write_heartbeat() {
  local ok=$1 err=$2 payload tmp deferred_lines update_skipped_lines
  HEARTBEAT_CYCLE=$((HEARTBEAT_CYCLE + 1))
  deferred_lines=$(printf '%s\n' "${CYCLE_DEFERRED[@]-}")
  update_skipped_lines=$(printf '%s\n' "${CYCLE_UPDATE_SKIPPED[@]-}")
  if ! payload=$(TS="$(heartbeat_now_iso)" CYCLE="$HEARTBEAT_CYCLE" OK="$ok" \
      NEW_PICKED="$CYCLE_NEW_PICKED" DRIFT_PICKED="$CYCLE_DRIFT_PICKED" \
      UPDATE_PICKED="$CYCLE_UPDATE_PICKED" ERR="$err" \
      PID="$POLLER_PID" PID_STARTED="$POLLER_STARTED" \
      REROUTED="$CYCLE_REROUTED" \
      DEFERRED_LINES="$deferred_lines" UPDATE_SKIPPED_LINES="$update_skipped_lines" python3 - <<'PY'
import json, os
err = os.environ["ERR"] or None
deferred = [line for line in os.environ["DEFERRED_LINES"].splitlines() if line]
update_skipped = [line for line in os.environ["UPDATE_SKIPPED_LINES"].splitlines() if line]
print(json.dumps({
    "ts": os.environ["TS"],
    "cycle": int(os.environ["CYCLE"]),
    "pid": int(os.environ["PID"]),
    "pid_started": os.environ["PID_STARTED"],
    "ok": os.environ["OK"] == "true",
    "new_picked": int(os.environ["NEW_PICKED"]),
    "drift_picked": int(os.environ["DRIFT_PICKED"]),
    "update_picked": int(os.environ["UPDATE_PICKED"]),
    "rerouted": int(os.environ["REROUTED"]),
    "err": err,
    "deferred": deferred,
    "update_skipped": update_skipped,
}, ensure_ascii=False, separators=(",", ":")))
PY
  ); then
    return 1
  fi
  tmp=$HEARTBEAT.tmp.$$
  printf '%s\n' "$payload" > "$tmp" || return 1
  mv "$tmp" "$HEARTBEAT"
}

compact_reason() {
  tr '\r\n\t' '   ' | sed 's/[[:space:]][[:space:]]*/ /g' | cut -c1-300
}

append_runner_error() {
  local no=$1
  local stage=$2
  local reason=$3
  local ts err_json decision_json alert_file
  ts=$(now_iso)
  reason=$(printf '%s' "$reason" | compact_reason)
  [ -n "$reason" ] || reason=unknown
  [ -n "$CYCLE_ERR" ] || CYCLE_ERR="$stage:$reason"

  err_json=$(NO="$no" STAGE="$stage" REASON="$reason" TS="$ts" python3 - <<'PY'
import json, os
print(json.dumps({
    "ts": os.environ["TS"], "changeset_no": os.environ["NO"],
    "overall": "RUNNER_ERR", "stage": os.environ["STAGE"],
    "reason": os.environ["REASON"], "source": "AUDITPOLLER"
}, ensure_ascii=False))
PY
)
  printf '%s\n' "$err_json" >> "$ERROR_LEDGER"

  decision_json=$(NO="$no" STAGE="$stage" REASON="$reason" TS="$ts" TAG="$RUN_TAG" python3 - <<'PY'
import json, os
print(json.dumps({
    "run_id": "", "run_tag": os.environ["TAG"], "flow": "changeset-audit",
    "changeset_no": os.environ["NO"], "verdicts": {}, "overall": "RUNNER_ERR",
    "model_calls": 0, "db_rows_written": 0, "wall_clock_sec": 0,
    "aborted": True, "runner_error_stage": os.environ["STAGE"],
    "runner_error_reason": os.environ["REASON"], "ts": os.environ["TS"]
}, ensure_ascii=False))
PY
)
  printf '%s\n' "$decision_json" >> "$DECISIONS"

  if validate_key "$no"; then
    alert_file=$ALERT_DIR/机检告警-$no.md
    if [ ! -e "$alert_file" ]; then
      {
        printf '# 机检告警 - %s - overall=RUNNER_ERR\n' "$no"
        printf -- '- 时间：%s · 来源：AUDITPOLLER\n' "$ts"
        printf -- '- 执行器错误：stage=%s · reason=%s\n' "$stage" "$reason"
        printf -- '- 处置：主窗检查调用契约；指纹未成功增行前不推进游标。\n'
      } > "$TMP_ROOT/alert.md"
      mv "$TMP_ROOT/alert.md" "$alert_file"
    fi
  fi

  printf '%s\n' "RUNNER_ERR $no $stage:$reason"
}

require_files() {
  local f
  for f in "$GRAPH" "$RUN_GRAPH" "$REG_GATE" "$REG_SETTLE" "$DRIFT_SCAN" "$FINGERPRINT" "$BASELINE_EXEMPT"; do
    if [ ! -f "$f" ]; then
      append_runner_error - preflight "missing_file=$f"
      return 1
    fi
  done
  if [ ! -f "$RUNTIME_DB" ]; then
    append_runner_error - preflight "missing_runtime_db=$RUNTIME_DB"
    return 1
  fi
  return 0
}

validate_no() {
  [[ "$1" =~ ^CS-[0-9]{8}-[0-9]{4}$ ]]
}

validate_update_time() {
  [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]
}

validate_key() {
  # Drift can legitimately requeue audited legacy exception keys (for example
  # REG-SEED-V1-20260804). Keep them out of the NEW cursor channel, but do not
  # make the drift channel silently lose them. This character set is also safe
  # for the graph's quoted SELECT and for alert filenames.
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]
}

write_cursor() {
  local no=$1 tmp
  validate_no "$no" || return 1
  tmp=$CURSOR.tmp.$$
  printf '%s\n' "$no" > "$tmp" || return 1
  mv "$tmp" "$CURSOR"
}

init_cursor() {
  local no
  if [ -f "$CURSOR" ]; then
    no=$(tr -d '\r\n' < "$CURSOR")
    if ! validate_no "$no"; then
      append_runner_error - cursor "invalid_cursor=$no"
      return 1
    fi
    return 0
  fi

  if ! no=$(python3 - "$FINGERPRINT" <<'PY'
import json, re, sys
lines = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if not lines:
    raise SystemExit("fingerprint ledger is empty")
obj = json.loads(lines[-1])
no = obj.get("changeset_no", "")
if not re.fullmatch(r"CS-[0-9]{8}-[0-9]{4}", no):
    raise SystemExit("last fingerprint has invalid changeset_no: %r" % no)
print(no)
PY
  ); then
    append_runner_error - cursor "cannot_initialize_from_last_fingerprint"
    return 1
  fi
  if ! write_cursor "$no"; then
    append_runner_error - cursor "cannot_write_initial_cursor=$no"
    return 1
  fi
  return 0
}

fetch_standard_changesets() {
  local err_file=$TMP_ROOT/mysql.err
  : > "$err_file"
  if ! "$DOCKER_BIN" exec "$MYSQL_CONTAINER" sh -c \
      'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 -N -B ${DB_NAME} -e "SELECT changeset_no FROM t_code_changeset WHERE is_del=0 AND changeset_no REGEXP '\''^CS-[0-9]{8}-[0-9]{4}$'\'' ORDER BY changeset_no"' \
      2>"$err_file"; then
    return 1
  fi
}

fetch_update_scan_snapshot() {
  local err_file=$TMP_ROOT/update-snapshot.err
  : > "$err_file"
  if ! "$DOCKER_BIN" exec "$MYSQL_CONTAINER" sh -c \
      'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 -N -B ${DB_NAME} -e "SELECT DATE_FORMAT(NOW(), '\''%Y-%m-%d %H:%i:%s'\'')"' \
      2>"$err_file"; then
    return 1
  fi
}

fetch_updated_standard_changesets() {
  local since=$1 until=$2 err_file=$TMP_ROOT/update-scan.err
  validate_update_time "$since" && validate_update_time "$until" || return 2
  : > "$err_file"
  if ! "$DOCKER_BIN" exec "$MYSQL_CONTAINER" sh -c \
      'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 -N -B ${DB_NAME} -e "SELECT changeset_no, DATE_FORMAT(update_time, '\''%Y-%m-%d %H:%i:%s'\'') FROM t_code_changeset WHERE is_del=0 AND changeset_no REGEXP '\''^CS-[0-9]{8}-[0-9]{4}$'\'' AND update_time > '\''$1'\'' AND update_time <= '\''$2'\'' ORDER BY update_time, changeset_no"' \
      update-scan "$since" "$until" 2>"$err_file"; then
    return 1
  fi
}

read_update_scan_state() {
  local stamp
  [ -f "$UPDATE_SCAN_STATE" ] || return 2
  stamp=$(tr -d '\r\n' < "$UPDATE_SCAN_STATE")
  validate_update_time "$stamp" || return 1
  printf '%s\n' "$stamp"
}

write_update_scan_state() {
  local stamp=$1 tmp
  validate_update_time "$stamp" || return 1
  tmp=$UPDATE_SCAN_STATE.tmp.$$
  printf '%s\n' "$stamp" > "$tmp" || return 1
  mv "$tmp" "$UPDATE_SCAN_STATE"
}

mark_update_completed() {
  local no=$1 update_time=$2 ts entry
  validate_no "$no" && validate_update_time "$update_time" || return 1
  ts=$(now_iso)
  if ! entry=$(TS="$ts" NO="$no" UPDATE_TIME="$update_time" python3 - <<'PY'
import json, os
print(json.dumps({
    "ts": os.environ["TS"],
    "changeset_no": os.environ["NO"],
    "update_time": os.environ["UPDATE_TIME"],
    "action": "UPDATE_JUDGED",
}, ensure_ascii=False, separators=(",", ":")))
PY
  ); then
    return 1
  fi
  printf '%s\n' "$entry" >> "$UPDATE_COMPLETED"
}

compact_update_completed() {
  local watermark=$1 tmp
  validate_update_time "$watermark" || return 1
  [ -f "$UPDATE_COMPLETED" ] || return 0
  tmp=$UPDATE_COMPLETED.tmp.$$
  if ! python3 - "$UPDATE_COMPLETED" "$watermark" > "$tmp" <<'PY'
import json, re, sys
path, watermark = sys.argv[1:]
no_re = re.compile(r"^CS-\d{8}-\d{4}$")
time_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
with open(path, encoding="utf-8") as fh:
    for line_no, raw in enumerate(fh, 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("invalid update completion JSONL line %d: %s" % (line_no, exc.msg))
        no, stamp, action = obj.get("changeset_no"), obj.get("update_time"), obj.get("action")
        if not isinstance(no, str) or not no_re.fullmatch(no) or not isinstance(stamp, str) or not time_re.fullmatch(stamp) or action != "UPDATE_JUDGED":
            raise SystemExit("invalid update completion JSONL line %d" % line_no)
        if stamp > watermark:
            print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
PY
  then
    return 1
  fi
  mv "$tmp" "$UPDATE_COMPLETED"
}

filter_update_candidates() {
  python3 - "$TMP_ROOT/update-window.raw" "$TMP_ROOT/update-drift.list" "$UPDATE_SCAN_FINGERPRINT_BASE" "$UPDATE_COMPLETED" "$TMP_ROOT/update-skipped.list" <<'PY'
import os, re, sys
import json
window_path, drift_path, fingerprint_path, completed_path, skipped_path = sys.argv[1:]
no_re = re.compile(r"^CS-\d{8}-\d{4}$")
time_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
covered = set()
with open(fingerprint_path, encoding="utf-8") as fh:
    for line_no, raw in enumerate(fh, 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("invalid fingerprint JSONL line %d: %s" % (line_no, exc.msg))
        no = obj.get("changeset_no")
        if isinstance(no, str) and no_re.fullmatch(no):
            covered.add(no)
completed = set()
if os.path.exists(completed_path):
    with open(completed_path, encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SystemExit("invalid update completion JSONL line %d: %s" % (line_no, exc.msg))
            no, stamp, action = obj.get("changeset_no"), obj.get("update_time"), obj.get("action")
            if not isinstance(no, str) or not no_re.fullmatch(no) or not isinstance(stamp, str) or not time_re.fullmatch(stamp) or action != "UPDATE_JUDGED":
                raise SystemExit("invalid update completion JSONL line %d" % line_no)
            completed.add((no, stamp))
changed = set()
with open(drift_path, encoding="utf-8") as fh:
    for raw in fh:
        bits = raw.split()
        if bits and no_re.fullmatch(bits[0]):
            changed.add(bits[0])
skipped = []
with open(window_path, encoding="utf-8") as fh:
    for line_no, raw in enumerate(fh, 1):
        bits = raw.rstrip("\r\n").split("\t")
        if len(bits) != 2 or not no_re.fullmatch(bits[0]) or not time_re.fullmatch(bits[1]):
            raise SystemExit("invalid update candidate line %d" % line_no)
        # A row without a pre-window fingerprint belongs to the NEW channel;
        # a changed fingerprint belongs to DRIFT. UPDATE owns only the stable
        # judged residue left by those two routes.
        if bits[0] in covered and bits[0] not in changed and tuple(bits) in completed:
            skipped.append(bits[0])
        elif bits[0] in covered and bits[0] not in changed:
            print("\t".join(bits))
with open(skipped_path, "w", encoding="utf-8") as fh:
    for no in dict.fromkeys(skipped):
        fh.write(no + "\n")
PY
}

prepare_update_channel() {
  local since until state_rc
  UPDATE_SCAN_READY=0
  UPDATE_SCAN_WINDOW_END=
  : > "$TMP_ROOT/update-skipped.list"
  if ! until=$(fetch_update_scan_snapshot); then
    append_runner_error - update_scan "snapshot:$(cat "$TMP_ROOT/update-snapshot.err" | compact_reason)"
    return 1
  fi
  until=$(printf '%s' "$until" | tr -d '\r\n')
  if ! validate_update_time "$until"; then
    append_runner_error - update_scan "invalid_snapshot=$until"
    return 1
  fi

  since=$(read_update_scan_state)
  state_rc=$?
  if [ "$state_rc" -eq 2 ]; then
    # A new installation has no safe historic watermark. Seed it from the
    # right boundary so old rows cannot trigger a replay storm; changes after
    # this snapshot fall in the next (strictly greater) window.
    if ! write_update_scan_state "$until"; then
      append_runner_error - update_scan "bootstrap_state_write_failed=$until"
      return 1
    fi
    if ! compact_update_completed "$until"; then
      append_runner_error - update_scan "bootstrap_completion_compact_failed=$until"
      return 1
    fi
    return 0
  fi
  if [ "$state_rc" -ne 0 ]; then
    append_runner_error - update_scan "invalid_state=$UPDATE_SCAN_STATE"
    return 1
  fi
  if ! compact_update_completed "$since"; then
    append_runner_error - update_scan "completion_compact_failed=$since"
    return 1
  fi

  if ! fetch_updated_standard_changesets "$since" "$until" > "$TMP_ROOT/update-window.raw"; then
    append_runner_error - update_scan "query:$(cat "$TMP_ROOT/update-scan.err" | compact_reason)"
    return 1
  fi
  UPDATE_SCAN_WINDOW_END=$until
  UPDATE_SCAN_READY=1
  if [ ! -s "$TMP_ROOT/update-window.raw" ]; then
    : > "$TMP_ROOT/update.list"
    return 0
  fi

  # The dry run is side-effect free and supplies the exact existing fingerprint
  # predicate. The filter reads the pre-NEW ledger snapshot, so a fingerprint
  # NEW writes earlier in this cycle cannot make the same unit an UPDATE item.
  if ! python3 "$DRIFT_SCAN" --scan --dry-run > "$TMP_ROOT/update-drift.list" \
      2> "$TMP_ROOT/update-drift.err"; then
    append_runner_error - update_scan "drift_compare:$(cat "$TMP_ROOT/update-drift.err" | compact_reason)"
    return 1
  fi
  if ! filter_update_candidates > "$TMP_ROOT/update.list" 2> "$TMP_ROOT/update-filter.err"; then
    append_runner_error - update_scan "filter:$(cat "$TMP_ROOT/update-filter.err" | compact_reason)"
    return 1
  fi
  CYCLE_UPDATE_SKIPPED=()
  while IFS= read -r no; do
    [ -n "$no" ] || continue
    if ! validate_no "$no"; then
      append_runner_error "$no" update_scan "invalid_completed_skip_key"
      return 1
    fi
    CYCLE_UPDATE_SKIPPED+=("$no")
  done < "$TMP_ROOT/update-skipped.list"
  return 0
}

collect_uncovered_standard_changesets() {
  python3 - "$TMP_ROOT/all-standard.list" "$FINGERPRINT" "$BASELINE_EXEMPT" <<'PY'
import json, re, sys
standard_path, fingerprint_path, baseline_exempt_path = sys.argv[1:]
pattern = re.compile(r"^CS-\d{8}-\d{4}$")
with open(standard_path, encoding="utf-8") as fh:
    standard = {line.strip() for line in fh if pattern.fullmatch(line.strip())}
covered = set()
with open(fingerprint_path, encoding="utf-8") as fh:
    for line_no, line in enumerate(fh, 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit("invalid fingerprint JSONL line %d: %s" % (line_no, exc.msg))
        no = obj.get("changeset_no")
        if isinstance(no, str) and pattern.fullmatch(no):
            covered.add(no)
baseline_exempt = set()
with open(baseline_exempt_path, encoding="utf-8") as fh:
    for raw in fh:
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        baseline_exempt.add(raw.split()[0])
for no in sorted(standard - covered - baseline_exempt):
    print(no)
PY
}

build_new_list() {
  local cursor=$1
  if ! collect_uncovered_standard_changesets > "$TMP_ROOT/pending-standard.list" 2> "$TMP_ROOT/pending-standard.err"; then
    return 1
  fi
  : > "$TMP_ROOT/pending-after-cursor.list"
  : > "$TMP_ROOT/pending-before-cursor.list"
  # Cursor is a performance ordering hint only: both sides remain in the collection difference.
  LC_ALL=C awk -v cursor="$cursor" -v after="$TMP_ROOT/pending-after-cursor.list" \
    -v before="$TMP_ROOT/pending-before-cursor.list" '{ if ($0 > cursor) print > after; else print > before }' \
    "$TMP_ROOT/pending-standard.list"
  LC_ALL=C sort -u "$TMP_ROOT/pending-after-cursor.list" > "$TMP_ROOT/new.list"
  LC_ALL=C sort -u "$TMP_ROOT/pending-before-cursor.list" >> "$TMP_ROOT/new.list"
}

advance_cursor_if_later() {
  local candidate=$1 current
  current=$(tr -d '\r\n' < "$CURSOR")
  if LC_ALL=C awk -v candidate="$candidate" -v current="$current" 'BEGIN { exit(candidate > current ? 0 : 1) }'; then
    write_cursor "$candidate"
  fi
  return 0
}

registration_is_settled() {
  local no=$1 output
  SETTLE_REASON=
  if output=$(python3 "$REG_SETTLE" "$no" --check-only 2> "$TMP_ROOT/settle.err"); then
    if [ "$output" = "SETTLED" ]; then
      return 0
    fi
    SETTLE_REASON="unexpected_output=$(printf '%s' "$output" | compact_reason)"
    return 1
  fi
  if [[ "$output" == UNSETTLED\ * ]]; then
    return "$DEFERRED"
  fi
  SETTLE_REASON=$(cat "$TMP_ROOT/settle.err" | compact_reason)
  [ -n "$SETTLE_REASON" ] || SETTLE_REASON="unexpected_output=$(printf '%s' "$output" | compact_reason)"
  return 1
}

clear_deferred() {
  local no=$1 i
  for ((i = 0; i < ${#DEFERRED_NOS[@]}; i++)); do
    if [ "${DEFERRED_NOS[$i]}" = "$no" ]; then
      # Keep the slot but reset its streak. This avoids an empty-array rebuild
      # under set -u and gives a later defer a fresh consecutive count.
      DEFERRED_COUNTS[$i]=0
      return 0
    fi
  done
  return 0
}

mark_deferred() {
  local no=$1 i streak=0 seen=0 cycle_seen=0
  # A requeue can surface in both channels during one run_cycle. It is still
  # one deferred *cycle*, so count and report a changeset at most once here.
  for ((i = 0; i < ${#CYCLE_DEFERRED[@]}; i++)); do
    if [ "${CYCLE_DEFERRED[$i]}" = "$no" ]; then
      cycle_seen=1
      break
    fi
  done
  if [ "$cycle_seen" -eq 1 ]; then
    return 0
  fi
  CYCLE_DEFERRED+=("$no")

  for ((i = 0; i < ${#DEFERRED_NOS[@]}; i++)); do
    if [ "${DEFERRED_NOS[$i]}" = "$no" ]; then
      streak=$((DEFERRED_COUNTS[$i] + 1))
      DEFERRED_COUNTS[$i]=$streak
      seen=1
      break
    fi
  done
  if [ "$seen" -eq 0 ]; then
    DEFERRED_NOS+=("$no")
    DEFERRED_COUNTS+=(1)
    streak=1
  fi

  # The 11th consecutive defer is the first illegal one. append_runner_error
  # records the watchdog fault, but this function still returns normally: the
  # caller's explicit DEFERRED branch must continue to later candidates.
  if [ "$streak" -eq $((MAX_DEFERRED_CYCLES + 1)) ]; then
    append_runner_error "$no" deferred "$no:deferred_exceeded count=$streak"
  fi
}

append_gate_result() {
  local gate_file=$1 ts normalized
  ts=$(now_iso)
  if ! normalized=$(TS="$ts" python3 - "$gate_file" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
obj["ts"] = os.environ["TS"]
print(json.dumps(obj, ensure_ascii=False))
PY
  ); then
    return 1
  fi
  printf '%s\n' "$normalized" >> "$GATE_LEDGER"
}

write_alert_if_needed() {
  local no=$1 source=$2 overall=$3 summary=$4 gate_verdict=$5 gate_json=$6
  local alert_file ts
  case "$overall:$gate_verdict" in
    PASS:PASS|N/A:PASS|SKIPPED_ALREADY_MERGED:PASS)
      return 1
      ;;
  esac

  alert_file=$ALERT_DIR/机检告警-$no.md
  [ -e "$alert_file" ] && return 0
  ts=$(now_iso)
  {
    printf '# 机检告警 - %s - overall=%s\n' "$no" "$overall"
    printf -- '- 时间：%s · 来源：%s\n' "$ts" "$source"
    printf -- '- 判决原文：%s\n' "$summary"
    printf -- '- A门（轮询时点，已按契约排除 A6）：%s · %s\n' "$gate_verdict" "$gate_json"
    printf -- '- 处置：主窗人工兜（NEEDS_HUMAN/FAIL=转校验官或承建方；RUNNER_ERR=查执行器）\n'
  } > "$TMP_ROOT/alert.md"
  mv "$TMP_ROOT/alert.md" "$alert_file"
  return 0
}

consume_alert() {
  local no=$1 out_file=$TMP_ROOT/consumer.out err_file=$TMP_ROOT/consumer.err
  printf '%s\n' "$no" > "$TMP_ROOT/consumer.list"
  : > "$out_file"
  : > "$err_file"
  ALERTDIR="$ALERT_DIR" python3 "$ALERT_CONSUMER" \
    --archive "$TMP_ROOT/consumer.list" --commit >"$out_file" 2>"$err_file"
}

verify_trace() {
  local no=$1 run_id=$2 count
  if ! count=$(sqlite3 "$RUNTIME_DB" \
    "SELECT COUNT(*) FROM trace_runs WHERE run_id='$run_id' AND run_tag='$RUN_TAG' AND changeset_no='$no';" \
    2>"$TMP_ROOT/sqlite.err"); then
    return 1
  fi
  [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -gt 0 ]
}

record_and_verify_fingerprint() {
  local no=$1 overall=$2 run_id=$3 before after
  before=$(wc -l < "$FINGERPRINT" | tr -d ' ')
  if ! python3 "$DRIFT_SCAN" --record "$no" --overall "$overall" --run-id "$run_id" \
      >"$TMP_ROOT/record.out" 2>"$TMP_ROOT/record.err"; then
    return 1
  fi
  after=$(wc -l < "$FINGERPRINT" | tr -d ' ')
  if ! [[ "$before" =~ ^[0-9]+$ && "$after" =~ ^[0-9]+$ ]] || [ "$after" -le "$before" ]; then
    return 1
  fi
  python3 - "$FINGERPRINT" "$before" "$no" "$run_id" "$overall" <<'PY'
import json, sys
path, before, no, run_id, overall = sys.argv[1], int(sys.argv[2]), *sys.argv[3:]
with open(path, encoding="utf-8") as fh:
    rows = [line for line in fh if line.strip()]
new = []
for line in rows[before:]:
    try:
        new.append(json.loads(line))
    except json.JSONDecodeError:
        pass
ok = any(x.get("changeset_no") == no and x.get("run_id") == run_id
         and x.get("overall") == overall for x in new)
raise SystemExit(0 if ok else 1)
PY
}

judge_one() {
  local no=$1 source=$2
  local graph_out=$TMP_ROOT/graph.out graph_err=$TMP_ROOT/graph.err
  local gate_out=$TMP_ROOT/gate.out gate_err=$TMP_ROOT/gate.err
  local meta summary run_id run_tag got_no overall gate_verdict gate_json reason alert_written=0 settle_status
  local supersede_output supersede_acted

  if ! validate_key "$no"; then
    append_runner_error "$no" input "invalid_changeset_no"
    return 1
  fi

  # Registration must be stable before any graph or A-gate work. An unsettled record is
  # a normal defer: no HOLD, no decision/fingerprint, and no cursor advancement this cycle.
  registration_is_settled "$no"
  settle_status=$?
  if [ "$settle_status" -eq "$DEFERRED" ]; then
    mark_deferred "$no"
    return "$DEFERRED"
  fi
  clear_deferred "$no"
  if [ "$settle_status" -ne 0 ]; then
    append_runner_error "$no" registration_settle "$SETTLE_REASON"
    return 1
  fi

  : > "$graph_out"
  : > "$graph_err"
  if [ "$OMIT_REQUIRED_INPUT" = "changeset_no" ]; then
    TRJ_DB_PATH="$RUNTIME_DB" python3 "$RUN_GRAPH" "$GRAPH" \
      --workdir "$WORKDIR" --run-tag "$RUN_TAG" >"$graph_out" 2>"$graph_err"
  else
    TRJ_DB_PATH="$RUNTIME_DB" python3 "$RUN_GRAPH" "$GRAPH" \
      --input "changeset_no=$no" --workdir "$WORKDIR" --run-tag "$RUN_TAG" \
      >"$graph_out" 2>"$graph_err"
  fi
  if [ "$?" -ne 0 ]; then
    reason=$(cat "$graph_err" "$graph_out" | compact_reason)
    if [ "$OMIT_REQUIRED_INPUT" = "changeset_no" ]; then
      reason="missing_required_input=changeset_no; $reason"
    fi
    append_runner_error "$no" run_graph "$reason"
    return 1
  fi

  if ! meta=$(python3 - "$graph_out" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
required = ("run_id", "run_tag", "changeset_no", "overall")
if any(not isinstance(obj.get(k), str) or not obj[k] for k in required):
    raise SystemExit("missing required summary field")
if not re.fullmatch(r"[0-9a-f]{16}", obj["run_id"]):
    raise SystemExit("invalid run_id")
if not isinstance(obj.get("db_rows_written"), int) or obj["db_rows_written"] <= 0:
    raise SystemExit("no runtime rows reported")
compact = json.dumps(obj, ensure_ascii=False)
print("\t".join((obj["run_id"], obj["run_tag"], obj["changeset_no"], obj["overall"], compact)))
PY
  ); then
    append_runner_error "$no" summary "invalid_or_empty_json"
    return 1
  fi
  IFS=$'\t' read -r run_id run_tag got_no overall summary <<EOF
$meta
EOF
  if [ "$run_tag" != "$RUN_TAG" ] || [ "$got_no" != "$no" ]; then
    append_runner_error "$no" summary "identity_mismatch"
    return 1
  fi
  if ! verify_trace "$no" "$run_id"; then
    reason=$(cat "$TMP_ROOT/sqlite.err" 2>/dev/null | compact_reason)
    append_runner_error "$no" trace "missing_exact_run_rows${reason:+:$reason}"
    return 1
  fi

  : > "$gate_out"
  : > "$gate_err"
  python3 "$REG_GATE" "$no" --json --no-a6 >"$gate_out" 2>"$gate_err"
  if ! gate_json=$(python3 - "$gate_out" "$no" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
if obj.get("changeset_no") != sys.argv[2] or obj.get("verdict") not in ("PASS", "HOLD"):
    raise SystemExit("invalid gate result")
print(json.dumps(obj, ensure_ascii=False))
PY
  ); then
    reason=$(cat "$gate_err" "$gate_out" | compact_reason)
    append_runner_error "$no" registration_gate "$reason"
    return 1
  fi
  gate_verdict=$(GATE_JSON="$gate_json" python3 -c 'import json,os; print(json.loads(os.environ["GATE_JSON"])["verdict"])')
  if ! append_gate_result "$gate_out"; then
    append_runner_error "$no" gate_ledger "append_failed"
    return 1
  fi

  printf '%s\n' "$summary" >> "$DECISIONS" || {
    append_runner_error "$no" decision_ledger "append_failed"
    return 1
  }

  if write_alert_if_needed "$no" "$source" "$overall" "$summary" "$gate_verdict" "$gate_json"; then
    alert_written=1
  fi
  if [ "$alert_written" -eq 1 ]; then
    if ! consume_alert "$no"; then
      reason=$(cat "$TMP_ROOT/consumer.err" "$TMP_ROOT/consumer.out" | compact_reason)
      append_runner_error "$no" alert_consumer "$reason"
      return 1
    fi
  fi

  if ! record_and_verify_fingerprint "$no" "$overall" "$run_id"; then
    reason=$(cat "$TMP_ROOT/record.err" "$TMP_ROOT/record.out" 2>/dev/null | compact_reason)
    append_runner_error "$no" fingerprint "not_appended_or_mismatched${reason:+:$reason}"
    return 1
  fi
  if ! supersede_output=$(ALERTDIR="$ALERT_DIR" SUPERSEDE_LEDGER="$SUPERSEDE_LEDGER" \
      python3 "$ALERT_CONSUMER" --supersede "$no" \
        --overall "$overall" --gate "$gate_verdict" --run-id "$run_id" --source "$source" --commit \
        2>"$TMP_ROOT/supersede.err"); then
    reason=$(cat "$TMP_ROOT/supersede.err" | compact_reason)
    [ -n "$reason" ] || reason="consumer_nonzero"
    append_runner_error "$no" alert_supersede "$reason"
    return 1
  fi
  if ! supersede_acted=$(SUPERSEDE_OUTPUT="$supersede_output" python3 - <<'PY'
import json, os
lines = [line for line in os.environ["SUPERSEDE_OUTPUT"].splitlines() if line.strip()]
if len(lines) != 1:
    raise SystemExit(1)
obj = json.loads(lines[0])
if not isinstance(obj, dict) or not isinstance(obj.get("acted"), bool):
    raise SystemExit(1)
print("true" if obj["acted"] else "false")
PY
  ); then
    reason=$(printf '%s' "$supersede_output" | compact_reason)
    append_runner_error "$no" alert_supersede "invalid_json_or_missing_acted${reason:+:$reason}"
    return 1
  fi
  case "$supersede_acted" in
    true|false) ;;
    *)
      append_runner_error "$no" alert_supersede "invalid_acted_value=$supersede_acted"
      return 1
      ;;
  esac

  printf '%s %s\n' "$no" "$overall"
  return 0
}

run_new_channel() {
  local cursor no
  cursor=$(tr -d '\r\n' < "$CURSOR")
  if ! fetch_standard_changesets > "$TMP_ROOT/all-standard.list"; then
    append_runner_error - mysql "$(cat "$TMP_ROOT/mysql.err" | compact_reason)"
    return 1
  fi
  if ! build_new_list "$cursor"; then
    append_runner_error - fingerprint_coverage "$(cat "$TMP_ROOT/pending-standard.err" | compact_reason)"
    return 1
  fi

  while IFS= read -r no; do
    [ -n "$no" ] || continue
    CYCLE_NEW_PICKED=$((CYCLE_NEW_PICKED + 1))
    judge_one "$no" NEW
    case "$?" in
      0) ;;
      "$DEFERRED")
        # A normal defer must not turn one unfinished registration into a
        # queue-head stop. The collection difference will offer it next cycle.
        continue
        ;;
      *)
        return 1
        ;;
    esac
    if ! advance_cursor_if_later "$no"; then
      append_runner_error "$no" cursor "advance_failed_after_fingerprint"
      return 1
    fi
  done < "$TMP_ROOT/new.list"
  return 0
}

run_drift_channel() {
  local no
  : > "$TMP_ROOT/drift.list"
  : > "$TMP_ROOT/drift.err"
  if ! python3 "$DRIFT_SCAN" --scan > "$TMP_ROOT/drift.list" 2> "$TMP_ROOT/drift.err"; then
    append_runner_error - drift_scan "$(cat "$TMP_ROOT/drift.err" | compact_reason)"
    return 1
  fi
  while IFS= read -r no; do
    [ -n "$no" ] || continue
    if ! validate_key "$no"; then
      append_runner_error "$no" drift_scan "invalid_requeue_key"
      return 1
    fi
    CYCLE_DRIFT_PICKED=$((CYCLE_DRIFT_PICKED + 1))
    judge_one "$no" DRIFT
    case "$?" in
      0) ;;
      "$DEFERRED") continue ;;
      *) return 1 ;;
    esac
  done < "$TMP_ROOT/drift.list"
  return 0
}

run_update_channel() {
  local no updated_at deferred_seen=0
  [ "$UPDATE_SCAN_READY" -eq 1 ] || return 0
  while IFS=$'\t' read -r no updated_at; do
    [ -n "$no" ] || continue
    if ! validate_no "$no" || ! validate_update_time "$updated_at"; then
      append_runner_error "$no" update_scan "invalid_update_candidate"
      return 1
    fi
    CYCLE_UPDATE_PICKED=$((CYCLE_UPDATE_PICKED + 1))
    judge_one "$no" UPDATE
    case "$?" in
      0)
        if ! mark_update_completed "$no" "$updated_at"; then
          append_runner_error "$no" update_scan "completion_write_failed=$updated_at"
          return 1
        fi
        ;;
      "$DEFERRED")
        deferred_seen=1
        continue
        ;;
      *) return 1 ;;
    esac
  done < "$TMP_ROOT/update.list"

  # Do not advance across an unsettled update candidate: it has no other
  # trigger when its fingerprint is unchanged. Completed windows advance to
  # the snapshot, never wall-clock time after work, so concurrent updates are
  # neither self-replayed in this window nor silently skipped.
  [ "$deferred_seen" -eq 0 ] || return 0
  if ! write_update_scan_state "$UPDATE_SCAN_WINDOW_END"; then
    append_runner_error - update_scan "state_write_failed=$UPDATE_SCAN_WINDOW_END"
    return 1
  fi
  if ! compact_update_completed "$UPDATE_SCAN_WINDOW_END"; then
    append_runner_error - update_scan "completion_compact_failed=$UPDATE_SCAN_WINDOW_END"
    return 1
  fi
  return 0
}

# ── p6: merged source that has not reached the local LIVE manifest ──────────

append_sidecar_decision() {
  local flow=$1 point=$2 overall=$3 alert_key=$4 detail=$5 ts entry
  ts=$(now_iso)
  if ! entry=$(TS="$ts" FLOW="$flow" POINT="$point" OVERALL="$overall" \
      ALERT_KEY="$alert_key" DETAIL="$detail" TAG="$RUN_TAG" python3 - <<'PY'
import json, os
print(json.dumps({
    "run_id": "", "run_tag": os.environ["TAG"], "flow": os.environ["FLOW"],
    "changeset_no": "", "verdicts": {os.environ["POINT"]: os.environ["OVERALL"]},
    "overall": os.environ["OVERALL"], "model_calls": 0, "db_rows_written": 0,
    "wall_clock_sec": 0, "aborted": False, "alert_key": os.environ["ALERT_KEY"],
    "detail": os.environ["DETAIL"], "ts": os.environ["TS"],
}, ensure_ascii=False, separators=(",", ":")))
PY
  ); then
    return 1
  fi
  printf '%s\n' "$entry" >> "$DECISIONS"
}

read_live_commit() {
  python3 - "$LIVE_JSON" <<'PY'
import json, re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    obj = json.load(fh)
commit = obj.get("commit")
if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("LIVE.json missing a 40-character commit")
print(commit)
PY
}

read_deploy_drift_state() {
  # 输出每行 "head<TAB>key"。**兼容旧的单值形态**：生产现存的 deploy-drift-state.json
  # 还是 {"head":…,"alert_key":…}，升级后第一拍必须读得懂它，否则一上来就报错。
  [ -f "$DEPLOY_DRIFT_STATE" ] || return 2
  python3 - "$DEPLOY_DRIFT_STATE" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
items = obj.get("open") if isinstance(obj, dict) else None
if items is None:                       # 旧形态：单条记录
    items = [obj] if isinstance(obj, dict) and obj.get("head") else []
out = []
for it in items:
    if not isinstance(it, dict):
        raise SystemExit("invalid deploy drift entry")
    head, key = it.get("head"), it.get("alert_key")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SystemExit("invalid deploy drift head")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", key):
        raise SystemExit("invalid deploy drift alert key")
    out.append(head + "\t" + key)
print("\n".join(out))
PY
}

write_deploy_drift_state() {
  # 集合形态：$1=live，$2=多行 "head<TAB>key"（空串 = 无未清告警）
  local live=$1 entries=${2-} tmp payload
  if ! payload=$(LIVE="$live" TS="$(now_iso)" ENTRIES="$entries" python3 - <<'PY'
import json, os
entries, seen = [], set()
for line in os.environ["ENTRIES"].split("\n"):
    line = line.strip()
    if not line:
        continue
    head, _, key = line.partition("\t")
    if key and key not in seen:
        seen.add(key)
        entries.append({"head": head, "alert_key": key})
print(json.dumps({"live_commit": os.environ["LIVE"], "ts": os.environ["TS"],
                  "open": entries}, ensure_ascii=False, separators=(",", ":")))
PY
  ); then
    return 1
  fi
  tmp=$DEPLOY_DRIFT_STATE.tmp.$$
  printf '%s\n' "$payload" > "$tmp" || return 1
  mv "$tmp" "$DEPLOY_DRIFT_STATE"
}

drift_head_is_deployed() {
  # 判据核心：**告警的 head 是不是已经被现行 live 包含**。
  # 原判据要求 live 追平到状态里那个 head，等价于「相等」。但部署是往前跳的
  # （一次带走波 67–84），中间每个 head 都不会有「正好等于」的那一刻，
  # 于是中间那些告警永远等不到自己的清除条件——<日期> 实测 43 张里 14 张就这么挂着，
  # 而那 14 张的 head 全部已在 live 里。祖先才是「这次部署带上去了没有」的正确问法。
  local head=$1 live=$2
  [ -n "$head" ] && [ -n "$live" ] || return 1
  git -C "$INTEGRATION_REPO" merge-base --is-ancestor "$head" "$live" 2>/dev/null
}

clear_one_drift_alert() {
  # 一行 local 里引用同一行刚赋的变量，在本机 bash + 中文路径下会拿到空值
  # （记忆里早有这条：`local no="$1" af=".../机检告警-$no.md"` 得到 `机检告警-.md`），
  # 叠上 set -u 就直接报 unbound。**拆成两句**。
  local key=$1 head=$2 live=$3
  local alert_file=$ALERT_DIR/机检告警-$key.md
  if [ -f "$alert_file" ] && ! /usr/bin/grep -q '^## 自动清除$' "$alert_file"; then
    # 只追加，不改写：文件里已有的「主窗处置」等人工段原样保留。
    {
      printf '\n## 自动清除\n'
      printf -- '- 时间：%s\n' "$(now_iso)"
      printf -- '- p6：本告警的 head 已包含在 LIVE 中（祖先判据）：head=%s live=%s\n' "$head" "$live"
    } >> "$alert_file" || return 1
  fi
  append_sidecar_decision "deploy-drift" "p6" "DEPLOY_DRIFT_CLEARED" "$key" \
    "live_commit=$live head=$head reason=ancestor_of_live" || return 1
  return 0
}

clear_deploy_drift_ancestors() {
  # 每拍都跑，**不再只在「本拍零漂移」时才跑**：旧告警的 head 可能早已进 live，
  # 而与此同时又有更新的 head 未部署——原实现把清除挂在零漂移条件下，那一刻永远等不到。
  local live=$1 state state_rc head key kept="" cleared=0 f fkey fhead
  state=$(read_deploy_drift_state)
  state_rc=$?
  if [ "$state_rc" -eq 0 ]; then
    while IFS=$'\t' read -r head key; do
      [ -n "$key" ] || continue
      if drift_head_is_deployed "$head" "$live"; then
        clear_one_drift_alert "$key" "$head" "$live" || return 1
        cleared=$((cleared + 1))
      else
        kept="$kept$head	$key
"
      fi
    done <<< "$state"
  elif [ "$state_rc" -ne 2 ]; then
    return 1
  fi

  # 扫描兜底：状态里已经丢了钥匙的那些（旧实现单值覆盖造成的孤儿）只能靠扫目录找回。
  # **没有这一道，改了状态结构也治不了存量。**
  for f in "$ALERT_DIR"/机检告警-DEPLOY-DRIFT-*.md; do
    [ -e "$f" ] || continue
    /usr/bin/grep -q '^## 自动清除$' "$f" && continue
    fkey=$(basename "$f" .md); fkey=${fkey#机检告警-}
    fhead=$(/usr/bin/grep -o 'head=[0-9a-f]\{40\}' "$f" | head -1 | cut -d= -f2)
    [ -n "$fhead" ] || continue
    if drift_head_is_deployed "$fhead" "$live"; then
      clear_one_drift_alert "$fkey" "$fhead" "$live" || return 1
      cleared=$((cleared + 1))
    else
      # 扫到但还不能清的，**补记进状态**：状态是「当前未清集合」的权威表述，
      # 只让它记自己建过的那些，它就永远反映不了真实未清面（旧实现的孤儿正是这么来的）。
      case "$kept" in
        *"	$fkey"*) ;;                       # 已在集合里，不重复
        *) kept="$kept$fhead	$fkey
" ;;
      esac
    fi
  done

  write_deploy_drift_state "$live" "$kept" || return 1
  DRIFT_CLEARED_THIS_CYCLE=$cleared
  return 0
}

run_deploy_drift_channel() {
  local live head state state_rc prior_head prior_key diff_file log_file meta api_count controller_count migration_count waves
  local key alert_file detail
  if ! live=$(read_live_commit 2>"$TMP_ROOT/deploy-live.err"); then
    append_runner_error - deploy_drift "live_manifest:$(cat "$TMP_ROOT/deploy-live.err" | compact_reason)"
    return 1
  fi
  if ! head=$(git -C "$INTEGRATION_REPO" rev-parse HEAD 2>"$TMP_ROOT/deploy-head.err"); then
    append_runner_error - deploy_drift "integration_head:$(cat "$TMP_ROOT/deploy-head.err" | compact_reason)"
    return 1
  fi
  if ! [[ "$head" =~ ^[0-9a-f]{40}$ ]]; then
    append_runner_error - deploy_drift "invalid_integration_head=$head"
    return 1
  fi

  # 先清后判：**每拍无条件跑一次祖先清除**。
  # 旧实现把清除挂在「本拍零漂移」分支里，于是只要还有新漂移，
  # 早已部署的旧告警就一直等不到被清的那一刻。
  if ! clear_deploy_drift_ancestors "$live"; then
    append_runner_error - deploy_drift "ancestor_clear_failed"
    return 1
  fi

  diff_file=$TMP_ROOT/deploy-diff.list
  log_file=$TMP_ROOT/deploy-log.list
  if ! git -C "$INTEGRATION_REPO" diff --name-only "$live..$head" > "$diff_file" 2>"$TMP_ROOT/deploy-diff.err"; then
    append_runner_error - deploy_drift "git_diff:$(cat "$TMP_ROOT/deploy-diff.err" | compact_reason)"
    return 1
  fi
  if ! git -C "$INTEGRATION_REPO" log --format=%s "$live..$head" > "$log_file" 2>"$TMP_ROOT/deploy-log.err"; then
    append_runner_error - deploy_drift "git_log:$(cat "$TMP_ROOT/deploy-log.err" | compact_reason)"
    return 1
  fi
  if ! meta=$(python3 - "$diff_file" "$log_file" <<'PY'
import re, sys
diff_path, log_path = sys.argv[1:]
api = controller = migration = 0
for raw in open(diff_path, encoding="utf-8"):
    path = raw.strip()
    if path.startswith("frontend/src/api/"):
        api += 1
    elif re.fullmatch(r"src/main/java/.+/controller/.+", path):
        controller += 1
    elif path.startswith("db/migrations/"):
        migration += 1
waves = set()
for subject in open(log_path, encoding="utf-8"):
    waves.update(int(x) for x in re.findall(r"(?:波|wave)\s*(\d+)", subject, flags=re.I))
wave_text = ",".join(str(x) for x in sorted(waves)) or "-"
print("\t".join((str(api), str(controller), str(migration), wave_text)))
PY
  ); then
    append_runner_error - deploy_drift "classify_failed"
    return 1
  fi
  IFS=$'\t' read -r api_count controller_count migration_count waves <<EOF
$meta
EOF
  if ! [[ "$api_count" =~ ^[0-9]+$ && "$controller_count" =~ ^[0-9]+$ && "$migration_count" =~ ^[0-9]+$ ]]; then
    append_runner_error - deploy_drift "invalid_classification=$meta"
    return 1
  fi
  if [ $((api_count + controller_count + migration_count)) -eq 0 ]; then
    return 0        # 清除已在上面每拍跑过，这里只是「本拍没有新漂移」
  fi

  state=$(read_deploy_drift_state)
  state_rc=$?
  if [ "$state_rc" -eq 0 ]; then
    # 集合形态：这个 head 已经在未清清单里就别重复建告警
    while IFS=$'\t' read -r prior_head prior_key; do
      [ "$prior_head" = "$head" ] && return 0
    done <<< "$state"
  elif [ "$state_rc" -ne 2 ]; then
    append_runner_error - deploy_drift "invalid_state=$DEPLOY_DRIFT_STATE"
    return 1
  fi

  key=DEPLOY-DRIFT-${head:0:12}
  alert_file=$ALERT_DIR/机检告警-$key.md
  detail="live_commit=$live head=$head waves=$waves api_files=$api_count controller_files=$controller_count migration_files=$migration_count"
  if [ ! -e "$alert_file" ]; then
    {
      printf '# 机检告警 - %s - overall=ALERT_DEPLOY_DRIFT\n' "$key"
      printf -- '- 时间：%s · 来源：AUDITPOLLER p6\n' "$(now_iso)"
      printf -- '- p6：ALERT_DEPLOY_DRIFT %s\n' "$detail"
      printf -- '- 处置：主窗核对部署波次；LIVE.json 追平集成 HEAD 后本告警自动清除。\n'
    } > "$TMP_ROOT/deploy-alert.md"
    mv "$TMP_ROOT/deploy-alert.md" "$alert_file"
    if ! append_sidecar_decision "deploy-drift" "p6" "ALERT_DEPLOY_DRIFT" "$key" "$detail"; then
      append_runner_error - deploy_drift "decision_append_failed"
      return 1
    fi
  fi
  # 追加进集合，而不是覆盖——**覆盖正是旧实现把旧告警钥匙弄丢的那一步**
  if [ "$state_rc" -eq 0 ] && [ -n "$state" ]; then
    state="$state
$head	$key"
  else
    state="$head	$key"
  fi
  if ! write_deploy_drift_state "$live" "$state"; then
    append_runner_error - deploy_drift "state_write_failed"
    return 1
  fi
  printf 'ALERT_DEPLOY_DRIFT %s\n' "$detail"
}

# ── p7: consume new persisted route-404 evidence only ──────────────────────

route404_mysql() {
  local sql=$1
  "$DOCKER_BIN" exec "$MYSQL_CONTAINER" sh -c \
    'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 -N -B ${DB_NAME} -e "$1"' \
    route404 "$sql"
}

read_route404_cursor() {
  [ -f "$ROUTE404_CURSOR" ] || { printf '0\t0\n'; return 0; }
  python3 - "$ROUTE404_CURSOR" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    obj = json.load(fh)
runtime = obj.get("t_runtime_error_log", 0)
anomaly = obj.get("t_request_anomaly_log", 0)
if not all(isinstance(value, int) and value >= 0 for value in (runtime, anomaly)):
    raise SystemExit("route404 cursor must contain non-negative integer table watermarks")
print("%d\t%d" % (runtime, anomaly))
PY
}

write_route404_cursor() {
  local runtime_max=$1 anomaly_max=$2 tmp payload
  if ! payload=$(RUNTIME_MAX="$runtime_max" ANOMALY_MAX="$anomaly_max" TS="$(now_iso)" python3 - <<'PY'
import json, os
print(json.dumps({
    "t_runtime_error_log": int(os.environ["RUNTIME_MAX"]),
    "t_request_anomaly_log": int(os.environ["ANOMALY_MAX"]),
    "ts": os.environ["TS"],
}, ensure_ascii=False, separators=(",", ":")))
PY
  ); then
    return 1
  fi
  tmp=$ROUTE404_CURSOR.tmp.$$
  printf '%s\n' "$payload" > "$tmp" || return 1
  mv "$tmp" "$ROUTE404_CURSOR"
}

run_route404_channel() {
  local tables cursor runtime_cursor anomaly_cursor runtime_max=0 anomaly_max=0 grouped key alert_file
  if ! tables=$(route404_mysql "SHOW TABLES LIKE '%error%'; SHOW TABLES LIKE '%anomaly%';" 2>"$TMP_ROOT/route404-tables.err"); then
    append_runner_error - route404 "table_discovery:$(cat "$TMP_ROOT/route404-tables.err" | compact_reason)"
    return 1
  fi
  if ! cursor=$(read_route404_cursor 2>"$TMP_ROOT/route404-cursor.err"); then
    append_runner_error - route404 "cursor_read:$(cat "$TMP_ROOT/route404-cursor.err" | compact_reason)"
    return 1
  fi
  IFS=$'\t' read -r runtime_cursor anomaly_cursor <<EOF
$cursor
EOF
  if ! [[ "$runtime_cursor" =~ ^[0-9]+$ && "$anomaly_cursor" =~ ^[0-9]+$ ]]; then
    append_runner_error - route404 "invalid_cursor=$cursor"
    return 1
  fi
  : > "$TMP_ROOT/route404.raw"

  if printf '%s\n' "$tables" | /usr/bin/grep -qx 't_runtime_error_log'; then
    if ! runtime_max=$(route404_mysql "SELECT COALESCE(MAX(id),0) FROM t_runtime_error_log WHERE is_del=0;" 2>"$TMP_ROOT/route404-runtime-max.err"); then
      append_runner_error - route404 "runtime_max:$(cat "$TMP_ROOT/route404-runtime-max.err" | compact_reason)"
      return 1
    fi
    runtime_max=$(printf '%s' "$runtime_max" | tr -d '\r\n')
    if ! [[ "$runtime_max" =~ ^[0-9]+$ ]]; then
      append_runner_error - route404 "invalid_runtime_max=$runtime_max"
      return 1
    fi
    if [ "$runtime_max" -gt "$runtime_cursor" ]; then
      if ! route404_mysql "SELECT 'runtime', id, COALESCE(NULLIF(request_uri,''),'<unknown>') FROM t_runtime_error_log WHERE is_del=0 AND id > $runtime_cursor AND id <= $runtime_max AND (COALESCE(error_message,'') LIKE '%4001%' OR COALESCE(error_type,'') LIKE '%NoHandlerFound%' OR COALESCE(error_message,'') LIKE '%No handler%' OR COALESCE(error_message,'') LIKE '%路由不存在%' OR COALESCE(error_message,'') LIKE '%404%') ORDER BY id;" >> "$TMP_ROOT/route404.raw" 2>"$TMP_ROOT/route404-runtime.err"; then
        append_runner_error - route404 "runtime_query:$(cat "$TMP_ROOT/route404-runtime.err" | compact_reason)"
        return 1
      fi
    fi
  fi

  if printf '%s\n' "$tables" | /usr/bin/grep -qx 't_request_anomaly_log'; then
    if ! anomaly_max=$(route404_mysql "SELECT COALESCE(MAX(id),0) FROM t_request_anomaly_log WHERE is_del=0;" 2>"$TMP_ROOT/route404-anomaly-max.err"); then
      append_runner_error - route404 "anomaly_max:$(cat "$TMP_ROOT/route404-anomaly-max.err" | compact_reason)"
      return 1
    fi
    anomaly_max=$(printf '%s' "$anomaly_max" | tr -d '\r\n')
    if ! [[ "$anomaly_max" =~ ^[0-9]+$ ]]; then
      append_runner_error - route404 "invalid_anomaly_max=$anomaly_max"
      return 1
    fi
    if [ "$anomaly_max" -gt "$anomaly_cursor" ]; then
      if ! route404_mysql "SELECT 'anomaly', id, COALESCE(NULLIF(request_uri,''),'<unknown>') FROM t_request_anomaly_log WHERE is_del=0 AND id > $anomaly_cursor AND id <= $anomaly_max AND (COALESCE(sample_response,'') LIKE '%4001%' OR COALESCE(sample_response,'') LIKE '%404%' OR COALESCE(sample_response,'') LIKE '%路由不存在%' OR COALESCE(sample_response,'') LIKE '%No handler%') ORDER BY id;" >> "$TMP_ROOT/route404.raw" 2>"$TMP_ROOT/route404-anomaly.err"; then
        append_runner_error - route404 "anomaly_query:$(cat "$TMP_ROOT/route404-anomaly.err" | compact_reason)"
        return 1
      fi
    fi
  fi

  if ! grouped=$(python3 - "$TMP_ROOT/route404.raw" <<'PY'
import collections, sys
counts = collections.Counter()
for line_no, raw in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    if not raw.strip():
        continue
    fields = raw.rstrip("\r\n").split("\t")
    if len(fields) != 3 or not fields[1].isdigit() or not fields[2]:
        raise SystemExit("invalid route404 SQL row %d" % line_no)
    counts[fields[2]] += 1
for path, count in sorted(counts.items()):
    print(path + "\t" + str(count))
PY
  ); then
    append_runner_error - route404 "grouping_failed"
    return 1
  fi

  if [ -n "$grouped" ]; then
    key=ROUTE-404-r${runtime_max}-a${anomaly_max}
    alert_file=$ALERT_DIR/机检告警-$key.md
    if [ ! -e "$alert_file" ]; then
      {
        printf '# 机检告警 - %s - overall=ALERT_ROUTE_404\n' "$key"
        printf -- '- 时间：%s · 来源：AUDITPOLLER p7\n' "$(now_iso)"
        while IFS=$'\t' read -r path count; do
          printf -- '- p7：ALERT_ROUTE_404 %s %s\n' "$path" "$count"
        done <<EOF
$grouped
EOF
        printf -- '- 处置：主窗核对前端 API 消费与已部署后端路由；cursor 已推进，重复轮询不重复告警。\n'
      } > "$TMP_ROOT/route404-alert.md"
      mv "$TMP_ROOT/route404-alert.md" "$alert_file"
      while IFS=$'\t' read -r path count; do
        if ! append_sidecar_decision "route-404" "p7" "ALERT_ROUTE_404" "$key" "path=$path count=$count"; then
          append_runner_error - route404 "decision_append_failed"
          return 1
        fi
        printf 'ALERT_ROUTE_404 %s %s\n' "$path" "$count"
      done <<EOF
$grouped
EOF
    fi
  fi
  if ! write_route404_cursor "$runtime_max" "$anomaly_max"; then
    append_runner_error - route404 "cursor_write_failed"
    return 1
  fi
}

reroute_pending_alerts() {
  # **B 门原来只在「写告警」那一刻跑一次**，而它的路由依赖「并线状态」——
  # 一个会随时间变化的条件。<日期> CS-<日期>-0020：告警写于 17:19，那时确实未并线，
  # 判 HUMAN_PENDING 不归档（正确）；17:50 后并线，从此再没有任何一处回头看一眼。
  # 判决与指纹都没变，漂移通路也不会重判它，B 门就再没机会跑。
  # 这一族的形状：**关闭条件会在未来变成真，而系统只在过去那一刻问过一次。**
  #
  # 只重跑「无任何 ## 段」的那些：数量小、天然自限（一被归档或被人处置就退出这个集合），
  # 且归档动作本身只追加不改写，人工处置段不会被碰。
  local f no list=$TMP_ROOT/reroute.list n=0
  : > "$list"
  for f in "$ALERT_DIR"/机检告警-*.md; do
    [ -e "$f" ] || continue
    /usr/bin/grep -q '^## ' "$f" && continue          # 已有任何处置段 = 不归本通路管
    no=$(basename "$f" .md); no=${no#机检告警-}
    # 只收标准变更单号：DEPLOY-DRIFT / ROUTE-404 不是登记单，喂给 B 路由没有意义
    [[ "$no" =~ ^CS-[0-9]{8}-[0-9]{4}$ ]] || continue
    printf '%s\n' "$no" >> "$list"
    n=$((n + 1))
  done
  CYCLE_REROUTED=$n
  [ "$n" -gt 0 ] || return 0
  : > "$TMP_ROOT/reroute.out"
  : > "$TMP_ROOT/reroute.err"
  if ! ALERTDIR="$ALERT_DIR" python3 "$ALERT_CONSUMER" \
      --archive "$list" --commit >"$TMP_ROOT/reroute.out" 2>"$TMP_ROOT/reroute.err"; then
    append_runner_error - alert_reroute \
      "$(cat "$TMP_ROOT/reroute.err" "$TMP_ROOT/reroute.out" | compact_reason)"
    return 1
  fi
  return 0
}

run_cycle() {
  local status=0 heartbeat_ok=true heartbeat_err=
  CYCLE_NEW_PICKED=0
  CYCLE_DRIFT_PICKED=0
  CYCLE_UPDATE_PICKED=0
  CYCLE_ERR=
  CYCLE_DEFERRED=()
  CYCLE_UPDATE_SKIPPED=()
  UPDATE_SCAN_READY=0
  UPDATE_SCAN_WINDOW_END=
  UPDATE_SCAN_FINGERPRINT_BASE=$TMP_ROOT/update-fingerprint-before.jsonl
  if ! cp "$FINGERPRINT" "$UPDATE_SCAN_FINGERPRINT_BASE"; then
    append_runner_error - update_scan "fingerprint_snapshot_copy_failed"
    status=1
  fi
  if [ "$status" -eq 0 ]; then
    run_new_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    prepare_update_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    run_drift_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    run_update_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    run_deploy_drift_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    run_route404_channel || status=1
  fi
  if [ "$status" -eq 0 ]; then
    reroute_pending_alerts || status=1
  fi
  if [ "$status" -ne 0 ] || [ -n "$CYCLE_ERR" ]; then
    heartbeat_ok=false
    heartbeat_err=${CYCLE_ERR:-cycle:unknown}
  fi
  write_heartbeat "$heartbeat_ok" "$heartbeat_err" || true
  return "$status"
}

require_files || exit 1
init_heartbeat_cycle

if [ "$MODE" = "audit_one" ]; then
  judge_one "$AUDIT_ONE" TEST
  exit $?
fi

init_cursor || exit 1

if [ "$MODE" = "once" ]; then
  run_cycle
  exit $?
fi

while :; do
  run_cycle || true
  sleep "$INTERVAL"
done
