"""Differential dependency tests: no network and no real model processes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ADAPTER = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ['STARFIX_UPSTREAM_ROOT'])
sys.path.insert(0, str(ADAPTER))
from platform_bridge import install

spec = importlib.util.spec_from_file_location('upstream', SOURCE / 'trajectory/runner/run_graph.py')
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)


class Dependencies(unittest.TestCase):
    def test_fallback_prompt_and_verdict_identical_to_original(self):
        answer = '{"pass":false,"reason":"fixture failure"}'
        calls = []
        def native(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, answer, '')
        state = lambda: SimpleNamespace(model_calls=0, graph={'flow': 'fixture'}, step_digests=['n0: evidence'])
        with patch('subprocess.run', native):
            expected = upstream.BaseRunner.call_fallback(state(), {'id': 'n1', 'comment': 'check'}, 'raw evidence', 'contract')
        original_prompt = calls[0][0][-1]
        def opencode(args, **kwargs):
            calls.append((args, kwargs))
            events = json.dumps({'type': 'text', 'part': {'type': 'text', 'text': answer}})
            return subprocess.CompletedProcess(args, 0, events + '\n', '')
        with patch('subprocess.run', opencode):
            install('fixture-bash', 'fixture-opencode', {'providerID': 'provider', 'modelID': 'fixed-reviewer'})
            actual = upstream.BaseRunner.call_fallback(state(), {'id': 'n1', 'comment': 'check'}, 'raw evidence', 'contract')
        self.assertEqual(expected, actual)
        command, options = calls[-1]
        self.assertEqual(command[-1], original_prompt)
        self.assertIn('provider/fixed-reviewer', command)
        self.assertNotIn('--session', command)
        self.assertNotIn('--continue', command)
        self.assertNotIn('--auto', command)
        self.assertEqual(options['timeout'], 180)

    def test_bash_substitution_does_not_change_arguments_or_timeout(self):
        calls = []
        def fake(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, 'READY', '')
        with patch('subprocess.run', fake):
            install('D:/Git With Spaces/bash.exe')
            subprocess.run(['/bin/bash', '-c', 'printf READY'], timeout=180, cwd='fixture')
        self.assertEqual(calls, [(['D:/Git With Spaces/bash.exe', '-c', 'printf READY'], {'timeout': 180, 'cwd': 'fixture'})])

    def test_missing_adjudicator_uses_original_failure_not_captain_pass(self):
        with patch('subprocess.run') as process:
            install('fixture-bash')
            state = SimpleNamespace(model_calls=0, graph={}, step_digests=[])
            result = upstream.BaseRunner.call_fallback(state, {'id': 'n1'}, '', '')
            process.assert_not_called()
        self.assertFalse(result['parse_ok'])
        self.assertIsNone(result['pass'])


if __name__ == '__main__':
    unittest.main()
