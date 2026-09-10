#!/usr/bin/env python3
"""E2EGATE A8 结构扫描夹具：基线两红、只读负例两绿、修正 profile 两绿。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CHECKS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHECKS_DIR))
import registration_gate as gate  # noqa: E402


class RegistrationGateA8FixtureTest(unittest.TestCase):

    def test_baseline_candidate_readonly_write_tests_fail(self) -> None:
        for path in gate.A8_BASELINE_TESTS:
            with self.subTest(path=path):
                ok, detail = gate._a8_fixture_check(path)
                self.assertFalse(ok, detail)

    def test_readonly_negative_fixtures_pass(self) -> None:
        for path in ("CandidateReadonlyHealthControllerTest.java", "sales/ledger/LedgerHarness.java"):
            with self.subTest(path=path):
                ok, detail = gate._a8_negative_fixture_check(path)
                self.assertTrue(ok, detail)

    def test_candidate_write_versions_pass(self) -> None:
        for path in gate.A8_BASELINE_TESTS:
            with self.subTest(path=path):
                ok, detail = gate._a8_fixture_check(path, corrected=True)
                self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
