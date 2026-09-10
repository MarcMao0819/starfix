# -*- coding: utf-8 -*-
import json
from pathlib import Path
import tempfile
import unittest

import packet
import sandbox
import score
import test_benchmark as benchmark_tests


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='captain-isolation-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        for name in set(x for items in packet.STAGES.values() for x in items):
            p = self.source / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('public fixture ' + name, encoding='utf-8')
        (self.source / 'examiner').mkdir()
        (self.source / 'examiner/answers.json').write_text('PRIVATE_FIXTURE_MARKER')
        (self.source / '.git').mkdir()
        (self.source / '.git/history').write_text('PRIVATE_HISTORY_MARKER')
        self.destination = self.root / 'candidate'
        self.seal = self.root / 'private/seal.json'

    def export(self, stage='l3'):
        return packet.export_packet(self.destination, self.seal, stage, self.source)

    def test_only_exact_stage_files_exported(self):
        s = self.export()
        self.assertEqual(set(s['files']), set(packet.STAGES['l3']))
        data = ''.join(p.read_text() for p in self.destination.rglob('*') if p.is_file())
        self.assertNotIn('PRIVATE_', data)
        r = packet.verify_packet(self.destination, s)
        self.assertTrue(r['packet_verified'])
        self.assertFalse(r['runtime_isolation_verified'])

    def test_extra_answer_rejected(self):
        s = self.export()
        (self.destination / 'answer.json').write_text('private')
        with self.assertRaises(ValueError): packet.verify_packet(self.destination, s)

    def test_empty_git_directory_rejected(self):
        s = self.export(); (self.destination / '.git').mkdir()
        with self.assertRaises(ValueError): packet.verify_packet(self.destination, s)

    def test_modified_public_material_rejected(self):
        s = self.export()
        (self.destination / packet.RULES).write_text('public plus hidden answer')
        with self.assertRaises(ValueError): packet.verify_packet(self.destination, s)

    def test_source_symlink_to_answers_rejected_before_export(self):
        p = self.source / packet.RULES; p.unlink()
        p.symlink_to(self.source / 'examiner/answers.json')
        with self.assertRaises(ValueError): self.export()
        self.assertFalse(self.destination.exists())

    def test_packet_symlink_rejected(self):
        s = self.export()
        (self.destination / 'shortcut').symlink_to(self.source / 'examiner')
        with self.assertRaises(ValueError): packet.verify_packet(self.destination, s)

    def test_seal_cannot_be_exported(self):
        self.seal = self.destination / 'seal.json'
        with self.assertRaises(ValueError): self.export()

    def test_new_destination_cannot_be_inside_repository(self):
        self.destination = self.source / 'out'
        with self.assertRaises(ValueError): self.export()

    def test_forged_seal_cannot_allow_extra_file(self):
        s = self.export(); s['files']['examiner/answers.json'] = 'fake'
        with self.assertRaises(ValueError): packet.verify_packet(self.destination, s)

    def test_existing_output_not_overwritten(self):
        self.export()
        with self.assertRaises(ValueError): self.export()


class QualificationTests(unittest.TestCase):
    def full(self):
        t = benchmark_tests.ScoreTests(); t.setUp(); t.full_synthetic()
        return t.b, t.d

    def test_same_host_read_access_invalidates_high_score(self):
        b, d = self.full()
        d['isolation']['examiner_files_unreadable'].update(status='fail', evidence=['fixture/canary-readable:1'])
        r = score.evaluate(b, d)
        self.assertEqual(r['score'], 100)
        self.assertEqual(r['status'], 'INVALID_ISOLATION')
        self.assertFalse(r['assessment_valid'])

    def test_known_answer_exposure_is_invalid_not_low_ability(self):
        b, d = self.full()
        d['contamination'].update(status='known_exposure', evidence=['fixture/answer-read:1'])
        self.assertEqual(score.evaluate(b, d)['status'], 'INVALID_CONTAMINATED')

    def test_no_isolation_probe_means_no_qualification(self):
        b, d = self.full()
        d['isolation']['tool_boundary']['status'] = 'unverified'
        r = score.evaluate(b, d)
        self.assertNotEqual(r['status'], 'READY_FOR_SUPERVISED_PILOT')

    def test_rubric_labels_not_returned_to_candidate(self):
        _, result = sandbox.act(sandbox.new_state('fleet'), {'op':'captain.edit_business'})
        self.assertEqual(result['violation'], 'role_violation')
        visible = sandbox.public_response(result)
        self.assertNotIn('violation', visible)
        self.assertFalse(visible['ok'])


if __name__ == '__main__':
    unittest.main()
