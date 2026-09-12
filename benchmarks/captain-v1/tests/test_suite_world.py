# -*- coding: utf-8 -*-
import unittest
import suite_world as world


class SuiteWorldTests(unittest.TestCase):
    def test_no_real_tools_or_future_events_in_message_store(self):
        s,_=world.l2_setup('S10');self.assertEqual(s['messages'],[])
        world.l2_transition('S10',0,s);self.assertEqual([m['id'] for m in s['messages']],['m1'])

    def test_dispatch_completes_and_releases_worker_only_when_due(self):
        s=world.make_state();s,r=world.step(s,{'op':'dispatch','task':'T-C','worker':'green','book':'/bench/tasks/T-C.json','branch':'feat/c','idempotency_key':'T-C'})
        self.assertTrue(r['ok']);self.assertEqual(s['workers']['green']['state'],'working')
        world.advance(s,29);self.assertEqual(s['tasks']['T-C']['status'],'working')
        world.advance(s,30);self.assertEqual(s['tasks']['T-C']['status'],'completed')
        self.assertEqual(s['workers']['green']['state'],'idle')
        self.assertEqual(s['files']['/bench/out/T-C-receipt.json']['status'],'PASS')

    def test_missing_book_cannot_produce_false_delivery(self):
        s=world.make_state();s,r=world.step(s,{'op':'dispatch','task':'T-C','worker':'green','book':'/bench/missing','branch':'feat/c','idempotency_key':'T-C'})
        self.assertFalse(r['ok']);self.assertEqual(s['dispatches'],[])

    def test_outbound_memory_requires_real_message_and_matching_person(self):
        s=world.make_state();s,r=world.step(s,{'op':'message.send','recipient_id':'person-quality','reply_to':'m1','text':'received'})
        mid=r['data']['message_id'];rec={'id':'ack','message_id':mid,'kind':'fact','text':'acknowledged'}
        s,r=world.step(s,{'op':'memory.append','person_id':'person-quality','record':rec});self.assertTrue(r['ok'])
        s,r=world.step(s,{'op':'memory.append','person_id':'person-production','record':rec});self.assertFalse(r['ok'])
        rec['id']='fake';rec['message_id']='sent-missing'
        s,r=world.step(s,{'op':'memory.append','person_id':'person-quality','record':rec});self.assertFalse(r['ok'])

    def test_first_answer_does_not_clear_second_dependency(self):
        s,_=world.l2_setup('S06');world.l2_transition('S06',1,s)
        s,r=world.step(s,{'op':'activator.answer','event':{'event_id':'answer-a','qid':'Q-A','answer':'A'}})
        self.assertTrue(r['ok']);self.assertEqual(s['tasks']['T-A']['blockers'],['Q-B'])

    def test_message_wrong_person_rejected_and_correct_person_stored(self):
        s,_=world.l2_setup('S10');world.l2_transition('S10',0,s)
        s,r=world.step(s,{'op':'message.send','recipient_id':'person-production','reply_to':'m1','text':'received','type':'ack'})
        self.assertFalse(r['ok']);self.assertEqual(s['sent_messages'],[])
        s,r=world.step(s,{'op':'message.send','recipient_id':'person-quality','reply_to':'m1','text':'received','type':'ack'})
        self.assertTrue(r['ok']);self.assertEqual(len(s['sent_messages']),1)

    def test_timer_and_discriminative_gate_really_vary(self):
        s=world.make_state();s,_=world.step(s,{'op':'timer.register','id':'hour','interval_seconds':3600,'kind':'hourly'})
        world.advance(s,3599);self.assertEqual(s['events'],[])
        world.advance(s,3600);self.assertEqual(len(s['events']),1)
        s,_=world.step(s,{'op':'gate.define','id':'g','kind':'discrimination'})
        s,r=world.step(s,{'op':'gate.test','id':'g','sample':{'observations':[0,0,0]}});self.assertFalse(r['data']['pass'])
        s,r=world.step(s,{'op':'gate.test','id':'g','sample':{'observations':[0,1,0]}});self.assertTrue(r['data']['pass'])

    def test_virtual_file_does_not_open_host_path(self):
        s=world.make_state();s,r=world.step(s,{'op':'file.write','path':'/etc/file','content':'x'})
        self.assertFalse(r['ok']);self.assertNotIn('/etc/file',s['files'])

    def test_discrimination_accepts_documented_array_and_rejects_bad_shape(self):
        s=world.make_state();s,_=world.step(s,{'op':'gate.define','id':'g','kind':'discrimination'})
        s,r=world.step(s,{'op':'gate.test','id':'g','sample':[0,1,0]});self.assertTrue(r['data']['pass'])
        s,r=world.step(s,{'op':'gate.test','id':'g','sample':{'observations':'bad'}});self.assertFalse(r['ok'])

    def test_task_material_paths_are_readable_fixtures(self):
        s,_=world.l2_setup('S04');s,r=world.step(s,{'op':'file.read','path':'/bench/work/order-ui/src/status.ts'})
        self.assertTrue(r['ok']);self.assertIn('label',r['data'])

    def test_ready_task_has_book_and_source(self):
        s,_=world.l2_setup('S06')
        for tid,t in s['tasks'].items():
            book=s['files'][t['book']];self.assertEqual(book['id'],tid)
            self.assertIn(book['read_allowlist'][0],s['files'])


if __name__=='__main__':unittest.main()
