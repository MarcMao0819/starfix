# -*- coding: utf-8 -*-
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

import prepare_grok as entry


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='captain-entry-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=self.root/'source.toml'
        self.config.write_text('[model.fixture]\nmodel = "fixture-model"\nbase_url = "https://model.example/v1"\napi_key = "PRIVATE_FIXTURE_VALUE"\napi_backend = "chat_completions"\n')

    def test_requires_exact_registered_model(self):
        with self.assertRaises(ValueError): entry.read_model(self.config,'other-model')

    def test_unsupported_secret_syntax_does_not_echo_value(self):
        self.config.write_text('[model.fixture]\napi_key = unexpected_PRIVATE_FIXTURE_VALUE\n')
        with self.assertRaises(ValueError) as e: entry.read_model(self.config,'fixture')
        self.assertNotIn('PRIVATE_FIXTURE_VALUE',str(e.exception))

    def test_preparation_never_copies_key_or_starts_inference(self):
        out=self.root/'exam'
        with patch.object(entry,'preflight',return_value={}) as preflight:
            result=entry.prepare(out,'fixture',self.config,'/usr/bin/true')
        self.assertFalse(result['started'])
        preflight.assert_called_once_with(out.resolve())
        for p in out.rglob('*'):
            if p.is_file() and p.name!='grok':
                self.assertNotIn('PRIVATE_FIXTURE_VALUE',p.read_text(encoding='utf-8'))
        config=(out/'client-state/config.toml').read_text()
        self.assertIn('env_key = "STARFIX_CANDIDATE_API_KEY"',config)
        self.assertNotIn('api_key =',config)

    def test_launch_uses_outer_sandbox_fresh_tools_and_env_only_secret(self):
        out=self.root/'exam'
        with patch.object(entry,'preflight',return_value={}):
            entry.prepare(out,'fixture',self.config,'/usr/bin/true')
        with patch.object(entry,'preflight',return_value={}), patch.object(entry.os,'chdir'), patch.object(entry.os,'execve') as execute:
            entry.launch(out)
        binary,argv,env=execute.call_args.args
        self.assertEqual(binary,'/usr/bin/sandbox-exec')
        self.assertNotIn('PRIVATE_FIXTURE_VALUE',' '.join(argv))
        self.assertEqual(env['STARFIX_CANDIDATE_API_KEY'],'PRIVATE_FIXTURE_VALUE')
        self.assertIn('--no-subagents',argv)
        self.assertIn('--disable-web-search',argv)
        self.assertIn('--system-prompt-override',argv)
        self.assertNotIn('--resume',argv)
        # Interactive TUI ignores this headless-only option; do not advertise false filtering.
        self.assertNotIn('--tools',argv)

    def test_environment_does_not_inherit_shared_auth_or_hook_config(self):
        original_home=entry.os.environ.get('HOME')
        with patch.dict(entry.os.environ,{'XAI_API_KEY':'private','GROK_CONFIG':'private','SSH_AUTH_SOCK':'private'}):
            env=entry.clean_env(self.root/'client')
        self.assertNotIn('XAI_API_KEY',env)
        self.assertNotIn('GROK_CONFIG',env)
        self.assertNotIn('SSH_AUTH_SOCK',env)
        self.assertEqual(env.get('HOME'),original_home)
        self.assertEqual(env['GROK_MEMORY'],'0')

    def test_dns_failure_blocks_before_candidate_start(self):
        out=self.root/'exam'
        with patch.object(entry,'preflight',return_value={}):
            entry.prepare(out,'fixture',self.config,'/usr/bin/true')
        failure=subprocess.CompletedProcess([],6,'000','Could not resolve host')
        with patch.object(entry.subprocess,'run',return_value=failure), self.assertRaises(ValueError):
            entry.network_preflight(out)
        report=json.loads((out/'examiner/network-preflight.json').read_text())
        self.assertEqual(report['transport'],'FAIL')
        self.assertFalse(report['inference_started'])

    def test_unauthenticated_endpoint_proves_transport_only(self):
        out=self.root/'exam'
        with patch.object(entry,'preflight',return_value={}):
            entry.prepare(out,'fixture',self.config,'/usr/bin/true')
        response=subprocess.CompletedProcess([],0,'401','')
        with patch.object(entry.subprocess,'run',return_value=response) as run:
            result=entry.network_preflight(out)
        self.assertEqual(result['transport'],'PASS')
        self.assertFalse(result['authentication_tested'])
        self.assertNotIn('STARFIX_CANDIDATE_API_KEY',run.call_args.kwargs['env'])
        candidate_policy=(out/'examiner/outer.sb').read_text()
        self.assertNotIn('/usr/bin/curl',candidate_policy)


if __name__=='__main__': unittest.main()
