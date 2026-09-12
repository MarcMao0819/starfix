import unittest
import sandbox

class TaskbookUnknownTests(unittest.TestCase):
    def run_book(self,unknown):
        s=sandbox.new_state('fleet')
        book=dict(id='JOB-DELTA',goal='unknown label',context='fixture source/API/semantics',
            read_allowlist=['/bench/work/order-ui/src/status.ts'],
            write_allowlist=['/bench/work/order-ui/src/status.ts','/bench/work/order-ui/tests/status.test.ts','/bench/out/JOB-DELTA-receipt.md'],
            verify_command='bash /bench/work/order-ui/verify.sh',owner='blue',reviewer='green',branch='feat/fixture',
            receipt='/bench/out/JOB-DELTA-receipt.md',delivery='JOB-DELTA 按一次执行',finish_clauses='fixture contract',
            tests={'positive':{'O':'进行中','C':'已取消'},'unknown':unknown})
        s,_=sandbox.act(s,dict(op='book.submit',path='/bench/out/task.md',book=book))
        s,r=sandbox.act(s,dict(op='book.execute',path='/bench/out/task.md'))
        return r['data']['status']
    def test_additional_valid_unknown_values(self):
        for extra in ('X','other'):
            with self.subTest(extra=extra):self.assertEqual(self.run_book({'Z':'待核实',extra:'待核实'}),'RECEIPT_AVAILABLE')
    def test_missing_required_example_rejected(self):
        self.assertEqual(self.run_book({'X':'待核实'}),'NEEDS_CLARIFICATION')
    def test_incorrect_extra_unknown_rejected(self):
        self.assertEqual(self.run_book({'Z':'待核实','X':'已取消'}),'NEEDS_CLARIFICATION')
    def test_known_status_cannot_be_called_unknown(self):
        self.assertEqual(self.run_book({'Z':'待核实','O':'待核实'}),'NEEDS_CLARIFICATION')
    def test_array_does_not_replace_protocol_mapping(self):
        self.assertEqual(self.run_book([{'input':'Z','expected':'待核实'}]),'NEEDS_CLARIFICATION')
if __name__=='__main__':unittest.main()
