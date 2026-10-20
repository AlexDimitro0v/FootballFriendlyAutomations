import unittest
from datetime import datetime, timezone
from core import (branch, target_date, choose, perform, Blocked,
                  CheckpointFailed, validate_cancelled, verified_reply_ids)


class Memory:
    def __init__(self): self.saved = 0
    def save(self): self.saved += 1


class Rules(unittest.TestCase):
    def test_boundaries(self):
        for n, expected in [(0,'cancel'), (6,'cancel'), (7,'nothing'), (9,'nothing'), (10,'confirm'), (25,'confirm')]:
            self.assertEqual(branch(n), expected)

    def test_unknown_is_not_zero(self):
        for n in [None, '', -1, True, 6.5]:
            with self.assertRaises(Blocked): branch(n)

    def test_summer_and_winter(self):
        for stamp, date in [('2026-10-16T08:50:00+00:00','2026-10-17'),
                            ('2026-10-30T09:50:00+00:00','2026-10-31')]:
            self.assertEqual(str(target_date(datetime.fromisoformat(stamp), {'latest_start':'11:00'})), date)

    def test_early_wrong_day_and_late(self):
        for stamp in ['2026-10-16T07:54:00+00:00', '2026-10-17T08:50:00+00:00', '2026-10-16T09:00:00+00:00']:
            with self.assertRaises(Blocked): target_date(datetime.fromisoformat(stamp), {'latest_start':'11:00'})

    def test_skip(self):
        self.assertIsNone(target_date(datetime(2026,10,9,9,tzinfo=timezone.utc),
                                    {'latest_start':'11:00','skip_dates':['2026-10-09']}))

    def test_manual_date(self):
        with self.assertRaises(Blocked):
            target_date(datetime(2026,10,16,9,tzinfo=timezone.utc), {'latest_start':'11:00'}, '2026-10-24', True)

    def test_branch_lock_and_manual_recheck(self):
        self.assertEqual(choose({'branch':'nothing'},6), 'cancel')
        self.assertEqual(choose({'branch':'cancel'},11), 'cancel')
        self.assertEqual(choose({'branch':'confirm'},0), 'confirm')

    def test_done_not_repeated(self):
        store, event, calls = Memory(), {}, []
        action = lambda: calls.append(1) or {'sent': True}
        perform(store,event,'send',action)
        perform(store,event,'send',action)
        self.assertEqual(calls,[1])

    def test_crash_not_resent(self):
        store, event = Memory(), {}
        def crash(): raise TimeoutError()
        with self.assertRaises(TimeoutError): perform(store,event,'send',crash)
        with self.assertRaises(Blocked): perform(store,event,'send',lambda: True)
        self.assertEqual(store.saved,1)

    def test_no_action_without_checkpoint(self):
        class Fail:
            def save(self): raise IOError()
        calls = []
        with self.assertRaises(CheckpointFailed): perform(Fail(),{},'send',lambda: calls.append(1))
        self.assertFalse(calls)

    def test_completion_checkpoint_failure_is_fatal(self):
        class FailSecond:
            saved = 0
            def save(self):
                self.saved += 1
                if self.saved == 2:
                    raise IOError('disk unavailable')
        calls = []
        with self.assertRaises(CheckpointFailed):
            perform(FailSecond(), {}, 'send', lambda: calls.append('sent') or {'sent': True})
        self.assertEqual(calls, ['sent'])

    def test_cancelled_event_requires_verified_automation_cancellation(self):
        for previous in [{}, {'branch': 'confirm'}, {
            'branch': 'cancel', 'steps': {'meetup_cancel': {'status': 'pending'}}
        }]:
            with self.assertRaises(Blocked):
                validate_cancelled({'cancelled': True}, previous)
        validate_cancelled({'cancelled': True}, {
            'branch': 'cancel', 'steps': {'meetup_cancel': {'status': 'done'}}
        })

    def test_resumed_reply_excludes_only_verified_prior_replies(self):
        self.assertEqual(verified_reply_ids({'steps': {
            'whatsapp_reply_0': {'status': 'done', 'evidence': {'message_id': 'first'}},
            'whatsapp_reply_1': {'status': 'pending'},
            'whatsapp_confirmation_text': {'status': 'done', 'evidence': {'message_id': 'other'}}
        }}), {'first'})

    def test_naive_time_is_rejected(self):
        with self.assertRaises(Blocked):
            target_date(datetime(2026, 10, 16, 10, 50), {'latest_start': '11:00'})

    def test_json_metadata_handles_null_and_list_types(self):
        from browser import json_events
        self.assertEqual(list(json_events({'@type': None})), [])
        event = {'@type': ['Event'], 'startDate': 'example'}
        self.assertEqual(list(json_events({'@graph': [event]})), [event])


if __name__ == '__main__': unittest.main()
