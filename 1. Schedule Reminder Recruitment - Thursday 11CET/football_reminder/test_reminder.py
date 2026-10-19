import json
import tempfile
import unittest
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import reminder as r
import browser as b


class ScheduleTests(unittest.TestCase):
    def test_dst_and_sofia(self):
        from zoneinfo import ZoneInfo
        for month, day, utc_hour in ((7, 2, 9), (12, 3, 10)):
            dt = datetime(2026, month, day, 11, tzinfo=r.GENEVA)
            self.assertEqual(r.scheduled_time(dt, sending=True), dt)
            self.assertEqual(dt.astimezone(timezone.utc).hour, utc_hour)
            self.assertEqual(dt.astimezone(ZoneInfo('Europe/Sofia')).hour, 12)

    def test_early_and_next_day_blocked(self):
        for dt in (datetime(2026, 10, 22, 10, 59, tzinfo=r.GENEVA),
                   datetime(2026, 10, 23, 11, tzinfo=r.GENEVA)):
            with self.assertRaises(r.Blocked):
                r.scheduled_time(dt, sending=True)

    def test_old_workflow_rerun_blocked(self):
        with self.assertRaises(r.Blocked):
            r.scheduled_time(datetime(2026, 10, 29, 12, tzinfo=r.GENEVA), sending=True,
                             origin=datetime(2026, 10, 22, 11, tzinfo=r.GENEVA))

    def test_late_same_day_and_preview(self):
        dt = datetime(2026, 10, 22, 23, 59, tzinfo=r.GENEVA)
        self.assertEqual(r.scheduled_time(dt, sending=True).hour, 11)
        self.assertEqual(r.scheduled_time(datetime(2026, 10, 17, tzinfo=r.GENEVA)).date(), date(2026, 10, 22))

    def test_fallback_exact_168_hours_over_dst(self):
        end = datetime(2026, 10, 29, 11, tzinfo=r.GENEVA)
        self.assertEqual(end.timestamp() - r.fallback_start(end).timestamp(), 168 * 3600)
        self.assertTrue(r.eligible(r.fallback_start(end), r.fallback_start(end), end))
        self.assertFalse(r.eligible(end + timedelta(seconds=1), r.fallback_start(end), end))

    def test_boundary_date_not_guessed(self):
        end = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        spec = {'format': '%b %d, %Y', 'pattern': r'Joined\s+(.+)', 'date_only': True}
        lo, hi = b.joined_interval('Joined Oct 15, 2026', spec)
        self.assertEqual(b.classify_interval(lo, hi, r.fallback_start(end), end), 'ambiguous')
        lo, hi = b.joined_interval('Joined Oct 16, 2026', spec)
        self.assertEqual(b.classify_interval(lo, hi, r.fallback_start(end), end), 'inside')


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'receipts.sqlite3'
        self.j = r.Journal(self.path)

    def tearDown(self):
        self.j.db.close()
        self.tmp.cleanup()

    def test_independent_checkpoints_and_missed_week(self):
        previous = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        end = datetime(2026, 11, 5, 11, tzinfo=r.GENEVA)
        self.j.checkpoint('meetup', previous)
        self.assertEqual(self.j.start('meetup', end), previous)
        self.assertEqual(self.j.start('whatsapp', end), r.fallback_start(end))

    def test_pending_survives_checkpoint_and_restart(self):
        member = {'id': '12345', 'evidence': 'Joined Oct 20', 'platform': 'meetup'}
        self.j.queue('meetup', member)
        self.j.checkpoint('meetup', datetime(2026, 10, 22, 11, tzinfo=r.GENEVA))
        self.j.db.close()
        self.j = r.Journal(self.path)
        self.assertEqual(self.j.pending('meetup'), [member])
        self.j.remove_pending('meetup', '12345')
        self.assertEqual(self.j.pending('meetup'), [])

    def test_import_codex_timezone_and_welcomes(self):
        path = Path(self.tmp.name) / 'legacy.json'
        path.write_text(json.dumps({'runs': [{'scheduled_at': '2026-10-15T21:00:00+03:00',
            'whatsapp': {'public_post': 'verified_sent_standalone_main_announcements',
                         'recipients': [{'id': '+41780000000', 'outcome': 'verified_sent'}]},
            'meetup': {'public_post': 'blocked_not_signed_in'}}]}), encoding='utf-8')
        self.j.import_codex(path)
        self.assertEqual(self.j.status('whatsapp:public:2026-10-15'), 'sent')
        self.assertEqual(self.j.status('whatsapp:welcome:+41780000000'), 'sent')
        self.assertIsNone(self.j.status('meetup:public:2026-10-15'))

    def test_uncertain_click_never_retried(self):
        adapter = FakeAdapter(fail=True)
        scheduled = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        with patch.object(r, 'now_local', return_value=scheduled):
            with self.assertRaises(RuntimeError):
                r.deliver(adapter, self.j, 'k', 'text', None, True, scheduled)
            self.assertEqual(self.j.status('k'), 'uncertain')
            with self.assertRaises(r.Blocked):
                r.deliver(adapter, self.j, 'k', 'text', None, True, scheduled)
        self.assertEqual(adapter.clicks, 1)

    def test_uncertain_reconciled_from_live_evidence(self):
        self.j.record('k', 'uncertain')
        adapter = FakeAdapter(duplicate=True)
        result = r.deliver(adapter, self.j, 'k', 'text', None, True, r.now_local())
        self.assertEqual(result, 'already_sent')
        self.assertEqual(self.j.status('k'), 'sent')
        self.assertEqual(adapter.clicks, 0)

    def test_dry_run_no_send_or_receipt(self):
        adapter = FakeAdapter()
        result = r.deliver(adapter, self.j, 'k', 'text', None, False, r.now_local())
        self.assertEqual(result, 'preview')
        self.assertIsNone(self.j.status('k'))
        self.assertEqual(adapter.clicks, 0)
        self.assertFalse(adapter.filled)

    def test_window_rechecked_before_click(self):
        adapter = FakeAdapter()
        scheduled = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        with patch.object(r, 'now_local', return_value=scheduled + timedelta(days=1)):
            with self.assertRaises(r.Blocked):
                r.deliver(adapter, self.j, 'k', 'text', None, True, scheduled)
        self.assertEqual(adapter.clicks, 0)

    def test_partial_coverage_keeps_checkpoint_but_sends_verified_members(self):
        scheduled = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        previous = scheduled - timedelta(days=7)
        self.j.checkpoint('whatsapp', previous)
        adapters = {}
        def factory(page, cfg, platform):
            a = MagicMock()
            a.page = page
            a.duplicate.return_value = False
            if platform == 'whatsapp':
                a.scan_members.return_value = ([{'id': '+41781234567', 'evidence': 'Joined Oct20'}],
                                                False, ['history unavailable'])
            else:
                a.scan_members.return_value = ([], True, [])
            adapters[platform] = a
            return a
        with patch.object(r, 'now_local', return_value=scheduled), \
             patch.object(b, 'discover_event', return_value='https://www.meetup.com/geneve-football-friendly/events/123/'), \
             patch.object(b, 'Adapter', side_effect=factory):
            report = r.run(MagicMock(), {'whatsapp': {}, 'meetup': {}}, self.j,
                           Path(self.tmp.name), True)
        self.assertEqual(self.j.start('whatsapp', scheduled), previous)
        self.assertEqual(self.j.start('meetup', scheduled), scheduled)
        self.assertEqual(report['platforms']['whatsapp']['welcomes_sent'], 1)
        self.assertEqual(self.j.pending('whatsapp'), [])

    def test_failed_private_send_remains_pending_after_complete_scan(self):
        scheduled = datetime(2026, 10, 22, 11, tzinfo=r.GENEVA)
        def factory(page, cfg, platform):
            a = MagicMock()
            a.page = page
            a.duplicate.return_value = False
            a.scan_members.return_value = ([{'id': '123', 'evidence': 'Joined Oct20'}], True, [])
            a.open_private.side_effect = r.Blocked('Private messaging unavailable')
            return a
        with patch.object(r, 'now_local', return_value=scheduled), \
             patch.object(b, 'discover_event', return_value='https://www.meetup.com/geneve-football-friendly/events/123/'), \
             patch.object(b, 'Adapter', side_effect=factory):
            r.run(MagicMock(), {'whatsapp': {}, 'meetup': {}}, self.j, Path(self.tmp.name), True)
        self.assertEqual(self.j.start('meetup', scheduled), scheduled)
        self.assertEqual(self.j.pending('meetup')[0]['id'], '123')

    def test_event_failure_prevents_all_actions(self):
        with patch.object(b, 'discover_event', side_effect=r.Blocked('cancelled')), \
             patch.object(b, 'Adapter') as adapter:
            with self.assertRaises(r.Blocked):
                r.run(MagicMock(), {'whatsapp': {}, 'meetup': {}}, self.j, Path(self.tmp.name), False)
            adapter.assert_not_called()


class FakeAdapter:
    def __init__(self, fail=False, duplicate=False):
        self.fail, self.is_duplicate, self.clicks, self.filled = fail, duplicate, 0, False
    def duplicate(self, text, since):
        return self.is_duplicate
    def prepare(self, text, fill):
        self.filled = fill
    def send(self, text, before_click):
        before_click()
        self.clicks += 1
        if self.fail:
            raise RuntimeError('Network lost after click')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://www.meetup.com/geneve-football-friendly/events/316732745/'
        self.description = 'Play 11:00–12:00 (arrive 10:45 am)'
        self.event = {'url': self.url, 'startDate': '2026-10-17T10:45:00+02:00',
            'endDate': '2026-10-17T12:00:00+02:00', 'eventStatus': 'https://schema.org/EventScheduled',
            'location': {'name': 'Campus des Nations'}, 'description': self.description}

    def test_valid_event(self):
        self.assertEqual(b.validate_event(self.event, self.url, date(2026, 10, 17), self.description), self.url)

    def test_cancelled_wrong_venue_or_time(self):
        for key, value in [('eventStatus', 'https://schema.org/EventCancelled'),
                           ('location', {'name': 'Other pitch'}),
                           ('endDate', '2026-10-17T13:00:00+02:00')]:
            with self.subTest(key=key), self.assertRaises(r.Blocked):
                b.validate_event(dict(self.event, **{key: value}), self.url, date(2026, 10, 17), self.description)

    def test_identity_requires_stable_id(self):
        self.assertEqual(b.identity('+41 78 123 45 67', 'whatsapp'), '+41781234567')
        self.assertEqual(b.identity('https://www.meetup.com/members/1234/', 'meetup'), '1234')
        with self.assertRaises(r.Blocked):
            b.identity('Alex', 'whatsapp')

    def test_default_config_cannot_send(self):
        cfg = json.loads((r.ROOT / 'config.json').read_text(encoding='utf-8'))
        self.assertFalse(cfg['sending_enabled'])
        with self.assertRaises(r.Blocked):
            b.validate_config(cfg)

    def test_message_paragraphs(self):
        self.assertEqual(len(r.PUBLIC.split('\n\n')), 3)
        self.assertIn('Friday at 10:45', r.PUBLIC)
        self.assertIn('Friday at 10:50', r.PUBLIC)

if __name__ == '__main__':
    unittest.main()
