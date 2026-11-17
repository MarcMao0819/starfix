#!/usr/bin/env bash
# 门禁新增结构判据的自测：各建一个含反例的临时仓，应 FAIL；干净仓应 PASS。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/tools/scrub-gate.sh"
TMP="$ROOT/.scrub-gate-selftest-$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
check_fail() {
  local name="$1" content="$2"
  local dir="$TMP/$name"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/bad.txt"
  if "$GATE" "$dir" >/dev/null 2>&1; then
    echo "UNEXPECTED PASS: $name"
    fail=$((fail + 1))
  else
    pass=$((pass + 1))
  fi
}
uuid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
check_fail lowercase-uuid "sess=${uuid}"
check_fail wave-number "$(printf '\xe5\x9c\xa8\xe9\xa3\x9e\xef\xbc\x9aW%s \xe7\xad\x89\xe5\x9b\x9e\xe6\x89\xa7' 000)"

clean="$TMP/clean"
mkdir -p "$clean"
printf '%s\n' 'placeholder only' > "$clean/ok.txt"
if "$GATE" "$clean" >/dev/null 2>&1; then
  pass=$((pass + 1))
else
  echo "UNEXPECTED FAIL: clean tree"
  fail=$((fail + 1))
fi

echo "scrub extra tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]