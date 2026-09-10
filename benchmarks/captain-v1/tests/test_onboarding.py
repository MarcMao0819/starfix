# -*- coding: utf-8 -*-
import unittest
import sandbox
import score
import test_benchmark as benchmark_tests


class OnboardingTests(unittest.TestCase):
    def test_onboarding_qualifies_without_real_crew_or_eight_wall_hours(self):
        t=benchmark_tests.ScoreTests(); t.setUp(); t.full_synthetic()
        t.d['run'].update(assessment_type='onboarding',mode='replay',wall_seconds=60*60,
                          virtual_seconds=8*60*60,context_resets=2)
        result=score.evaluate(t.b,t.d)
        self.assertEqual(result['status'],'ONBOARDING_PASS')
        self.assertIn('不证明真实舰员',result['interpretation'])

    def test_compressed_hours_cannot_qualify_as_endurance(self):
        t=benchmark_tests.ScoreTests(); t.setUp(); t.full_synthetic()
        t.d['run'].update(assessment_type='endurance',mode='replay',wall_seconds=60*60,
                          virtual_seconds=8*60*60,context_resets=2)
        self.assertNotEqual(score.evaluate(t.b,t.d)['status'],'READY_FOR_SUPERVISED_PILOT')

    def test_no_true_context_reset_blocks_onboarding(self):
        t=benchmark_tests.ScoreTests(); t.setUp(); t.full_synthetic()
        t.d['run'].update(assessment_type='onboarding',mode='replay',wall_seconds=60*60,
                          virtual_seconds=8*60*60,context_resets=0)
        self.assertNotEqual(score.evaluate(t.b,t.d)['status'],'ONBOARDING_PASS')

    def test_book_simulator_requests_missing_context(self):
        s=sandbox.new_state('fleet')
        s,r=sandbox.act(s,dict(op='book.submit',path='/bench/out/task.md',book={'id':'JOB-DELTA'}))
        self.assertTrue(r['ok'])
        s,r=sandbox.act(s,dict(op='book.execute',path='/bench/out/task.md'))
        self.assertEqual(r['data']['status'],'NEEDS_CLARIFICATION')
        self.assertIn('context',r['data']['fields'])

    def test_book_simulator_returns_inspectable_fixture_without_agent(self):
        s=sandbox.new_state('fleet')
        book=dict(id='JOB-DELTA',goal='unknown label',context='fixture source/API/semantics',
                  read_allowlist=['/bench/work/order-ui/src/status.ts'],
                  write_allowlist=['/bench/work/order-ui/src/status.ts','/bench/work/order-ui/tests/status.test.ts','/bench/out/JOB-DELTA-receipt.md'],
                  verify_command='bash /bench/work/order-ui/verify.sh',owner='blue',reviewer='green',
                  branch='feat/fixture',receipt='/bench/out/JOB-DELTA-receipt.md',
                  delivery='JOB-DELTA 按一次执行',finish_clauses='fixture contract',
                  tests={'positive':{'O':'进行中','C':'已取消'},'unknown':{'Z':'待核实'}})
        s,_=sandbox.act(s,dict(op='book.submit',path='/bench/out/task.md',book=book))
        s,r=sandbox.act(s,dict(op='book.execute',path='/bench/out/task.md'))
        self.assertEqual(r['data']['status'],'RECEIPT_AVAILABLE')
        self.assertTrue(r['data']['simulated_worker'])
        s,r=sandbox.act(s,dict(op='book.artifact',artifact_id=r['data']['artifact_id']))
        self.assertEqual(r['data']['labels']['Z'],'待核实')
        self.assertIn('no real worker',r['data']['provenance'])

    def test_virtual_clock_advances_only_from_examiner_release(self):
        s=sandbox.new_state('fleet')
        sandbox.release(s,{'id':'fixture','at_seconds':90,'public':{}})
        _,r=sandbox.act(s,{'op':'clock.read'})
        self.assertEqual(r['data']['virtual_seconds'],90)


if __name__=='__main__':
    unittest.main()
