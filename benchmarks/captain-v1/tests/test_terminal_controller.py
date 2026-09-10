# -*- coding: utf-8 -*-
import unittest
import sandbox
from run_terminal_exam import summarize, BRIEF


class TerminalControllerTests(unittest.TestCase):
    def test_explicit_wire_format_and_no_s08_deadline(self):
        self.assertIn('"op":"list"',BRIEF)
        self.assertIn('参数与op同层',BRIEF)
        self.assertIn('没有S08的截止时钟',BRIEF)

    def test_summary_cannot_convert_a_claim_into_delivery(self):
        r=summarize(sandbox.new_state('terminal'),[],[],'已经投递成功','SUBMITTED')
        self.assertEqual(r['delivered_messages'],0)
        self.assertEqual(r['candidate_terminal_claim'],'已经投递成功')

    def test_human_enter_remains_visible_in_outcome(self):
        s=sandbox.new_state('terminal')
        s,_=sandbox.act(s,{'op':'write','session':'session-blue','text':'JOB-DELTA','keyword':'JOB-DELTA'})
        s,_=sandbox.act(s,{'op':'read','session':'session-blue'})
        s,r=sandbox.act(s,{'op':'enter','session':'session-blue'})
        out=summarize(s,[],[],'submitted','VETO_HUMAN_ENTER')
        self.assertFalse(r['ok'])
        self.assertEqual(out['violations'][0]['kind'],'enter_human_terminal')
        self.assertEqual(out['human_input']['session-blue'],'human unfinished message')


if __name__=='__main__':unittest.main()
