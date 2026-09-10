#!/usr/bin/env python3
"""兼容性钉子：run_graph 必须同时读旧/新 lineending_check 逐行输出。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fleetreg_run_graph", ROOT / "run_graph.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


OLD = "status=M before_cr=10 after_cr=11 added_crlf=1 deleted_crlf=0 verdict=PASS path=old.txt"
NEW = "status=M before_cr=10 after_cr=11 before_lines=10 after_lines=11 added_crlf=1 deleted_crlf=0 abs=PASS verdict=PASS path=new.txt"


def main() -> int:
    rows = MODULE.parse_lineending_output(OLD + "\n" + NEW + "\n" + "overall_verdict=PASS checked=2 fail=0 ambiguous=0 skipped=0")
    assert len(rows) == 2, rows
    assert rows[0]["path"] == "old.txt" and rows[0]["verdict"] == "PASS", rows[0]
    assert rows[0]["before_lines"] is None and rows[0]["after_lines"] is None and rows[0]["abs"] is None, rows[0]
    assert rows[1]["path"] == "new.txt" and rows[1]["verdict"] == "PASS" and rows[1]["abs"] == "PASS", rows[1]
    assert rows[1]["before_lines"] == "10" and rows[1]["after_lines"] == "11", rows[1]
    print("LINEENDING_OUTPUT_PARSER_SELFTEST_PASS old=1 new=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
