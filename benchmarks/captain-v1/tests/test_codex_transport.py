import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_codex_exam as transport


class CodexTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ('examiner','client-state','candidate'):(self.root/name).mkdir()
        (self.root/'examiner/launch.json').write_text(json.dumps({'model_config_id':'candidate-model','reasoning_effort_override':'high','per_turn_timeout_seconds':600}))

    def call(self,extra=(),model='candidate-model',effort='high'):
        answer=json.dumps({'actions':[],'done':True,'summary':'ready','reasoning_depth':{'level':'unknown','basis':'not visible'}})
        events=[{'type':'thread.started','thread_id':'fresh-session'},
            {'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING_CANARY'}},
            *extra,{'type':'item.completed','item':{'type':'agent_message','text':answer}},
            {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}]
        x=SimpleNamespace(stdout='\n'.join(json.dumps(e) for e in events),stderr='PRIVATE_DIAGNOSTIC_CANARY',returncode=0)
        with patch.object(transport.subprocess,'run',return_value=x),patch.object(transport,'runtime_metadata',return_value={'model':model,'effort':effort}):
            return transport.candidate_turn(self.root,'logical-session','public question',self.root/'examiner/round','unused','candidate-model',fresh=True)

    def test_reasoning_and_diagnostic_content_not_archived(self):
        result,meta=self.call()
        self.assertTrue(result['done']);self.assertEqual(meta['reasoning_depth']['runtime_reported'],'high')
        stored='\n'.join(p.read_text() for p in (self.root/'examiner').rglob('*') if p.is_file())
        self.assertNotIn('PRIVATE_REASONING_CANARY',stored);self.assertNotIn('PRIVATE_DIAGNOSTIC_CANARY',stored)

    def test_native_tool_action_rejected_before_simulation(self):
        with self.assertRaisesRegex(ValueError,'boundary failed'):
            self.call([{'type':'item.completed','item':{'type':'command_execution','command':'cat answer'}}])

    def test_cli_error_item_is_not_a_native_tool(self):
        result,meta=self.call([{'type':'item.completed','item':{'type':'error','message':'configuration notice'}}])
        self.assertTrue(result['done']);self.assertEqual(meta['tools'],[])

    def test_wrong_runtime_effort_rejected(self):
        with self.assertRaisesRegex(ValueError,'verification failed'):self.call(effort='medium')

    def test_unknown_resume_cannot_silently_start_fresh(self):
        with self.assertRaisesRegex(ValueError,'unknown candidate session'):
            transport.candidate_turn(self.root,'unknown','q',self.root/'examiner/round','unused','candidate-model')

    def test_environment_does_not_inherit_provider_or_host_tokens(self):
        with patch.dict(transport.os.environ,{'OPENAI_API_KEY':'PRIVATE','GITNEXUS_MCP_BEARER_TOKEN':'PRIVATE'}):
            env=transport.environment(self.root)
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('GITNEXUS_MCP_BEARER_TOKEN',env)
        self.assertEqual(env['HOME'],str(self.root/'client-state'))


if __name__=='__main__':unittest.main()
