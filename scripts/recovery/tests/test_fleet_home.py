# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = [
    'adopt-open.py',
    'resume-inplace.py',
    'restore-fleet.py',
    'open-steward.py',
    'send-text.py',
]


class FleetHomeRequired(unittest.TestCase):
    def test_python_scripts_exit_without_fleet_home(self):
        env = {k: v for k, v in os.environ.items() if k != 'FLEET_HOME'}
        for name in SCRIPTS:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, name)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0, name)
            self.assertIn('FLEET_HOME', proc.stdout + proc.stderr, name)


if __name__ == '__main__':
    unittest.main()