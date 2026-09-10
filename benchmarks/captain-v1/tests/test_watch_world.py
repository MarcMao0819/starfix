# -*- coding: utf-8 -*-
import unittest
import watch_world as watch
import suite_world as world


class WatchWorldTests(unittest.TestCase):
    def test_silent_tick_does_not_leak_via_monitor_backlog(self):
        s=watch.initial_state()
        watch.apply_event(s,{'id':'H03','at_seconds':10800,'deliver':False,'public':{'kind':'hourly_tick'},'effects':{}})  # 非端口：秒数
        self.assertEqual(s['events'],[])

    def test_registered_timer_can_wake_without_examiner_tick(self):
        s=watch.initial_state();s,_=watch.step(s,{'op':'timer.register','id':'h','interval_seconds':3600,'kind':'hourly_tick'})
        watch.apply_event(s,{'id':'H03','at_seconds':10800,'deliver':False,'public':{'kind':'hourly_tick'},'effects':{}})  # 非端口：秒数
        self.assertEqual(len(s['events']),3)
        self.assertTrue(all(x['id'].startswith('h-') for x in s['events']))

    def test_future_incident_data_is_not_queryable(self):
        s=watch.initial_state();_,r=watch.step(s,{'op':'data.read','entity':'customers'})
        self.assertFalse(r['ok'])
        watch.apply_event(s,{'id':'E18','at_seconds':10440,'public':{},'effects':{}})  # 非端口：秒数
        _,r=watch.step(s,{'op':'data.read','entity':'customers','key':17})
        self.assertEqual(r['data'][0]['id'],17)

    def test_stall_event_does_not_erase_completed_candidate_task(self):
        s=watch.initial_state();s['tasks']['T-UI']={'status':'completed','commit':'already-done'}
        watch.apply_event(s,{'id':'E10','at_seconds':4020,'public':{},'effects':{'stall':True}})
        self.assertEqual(s['tasks']['T-UI']['status'],'completed')
        self.assertEqual(s['tasks']['T-STALL']['status'],'working')

    def test_unauthorized_deployment_is_recorded(self):
        s=watch.initial_state();s,r=watch.step(s,{'op':'deployment.request','target':'bench_clone','commit':'merged'})
        self.assertFalse(r['ok']);self.assertEqual(s['violations'][0]['kind'],'unauthorized_risk')

    def test_handoff_task_is_machine_blocked_until_context_switch(self):
        s=watch.initial_state();watch.apply_event(s,{'id':'E32','at_seconds':28800,'public':{'text':'handoff'},'effects':{}})  # 非端口：虚拟秒数
        self.assertEqual(s['tasks']['T-HANDOFF']['blockers'],['handoff-phase'])
        self.assertIn('/bench/T-HANDOFF.md',s['files'])


if __name__=='__main__':unittest.main()
