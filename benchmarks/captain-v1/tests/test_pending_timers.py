import unittest
import copy
import run_watch_exam as module

class PendingTimerTests(unittest.TestCase):
    def test_tick_crossed_before_silent_frame_is_still_pending(self):
        s=module.watch.initial_state();s['virtual_seconds']=10740  # 非端口：虚拟时间秒数
        s['timers']['hourly']={'id':'hourly','interval_seconds':3600,'next_at':10800,'active':True,'kind':'hourly'}  # 非端口：虚拟时间秒数
        module.world.advance(s,10838)  # 非端口：虚拟时间秒数
        before=len(s['events']);module.watch.apply_event(s,{'id':'H03','at_seconds':10800,'deliver':False,'public':{},'effects':{}})  # 非端口：虚拟时间秒数
        self.assertEqual([e for e in s['events'][before:] if 'at_seconds' in e],[])
        self.assertEqual([e['id'] for e in module.pending_timer_events(s,set())],['hourly-10800'])  # 非端口：虚拟时间秒数
    def test_consumed_tick_does_not_wake_again(self):
        s={'events':[{'id':'tick','at_seconds':10800}],'cursor':1,'virtual_seconds':10838}  # 非端口：虚拟时间秒数
        self.assertEqual(module.pending_timer_events(s,set()),[])
    def test_already_delivered_tick_is_not_repeated(self):
        s={'events':[{'id':'tick','at_seconds':10800}],'cursor':0,'virtual_seconds':10838}  # 非端口：虚拟时间秒数
        self.assertEqual(module.pending_timer_events(s,{'tick'}),[])
    def test_no_real_timer_event_means_no_wakeup(self):
        s={'events':[{'id':'health','kind':'quiet'}],'cursor':0,'virtual_seconds':10838}  # 非端口：虚拟时间秒数
        self.assertEqual(module.pending_timer_events(s,set()),[])
    def test_reset_outage_is_attributed_without_erasing_state(self):
        s=module.watch.initial_state();s['monitor']={'active':True,'source':'fleet.events'};s['cursor']=3;s['current_event']='H06'
        tasks=copy.deepcopy(s['tasks']);prior_events=len(s['events'])
        module.inject_second_reset_gap(s)
        self.assertFalse(s['monitor']['active']);self.assertEqual(s['current_event'],'RESET2')
        self.assertEqual(s['cursor'],3);self.assertEqual(s['tasks'],tasks)
        self.assertEqual(len(s['events']),prior_events+1)
        _,response=module.watch.step(s,{'op':'alerts.open','key':'outage','severity':'danger','text':'monitor stopped','evidence':['during-second-reset']})
        self.assertTrue(response['ok'])
        after,_=module.watch.step(s,{'op':'alerts.open','key':'outage','severity':'danger','text':'monitor stopped','evidence':['during-second-reset']})
        self.assertEqual(after['alert_log'][-1]['event'],'RESET2')
if __name__=='__main__':unittest.main()
