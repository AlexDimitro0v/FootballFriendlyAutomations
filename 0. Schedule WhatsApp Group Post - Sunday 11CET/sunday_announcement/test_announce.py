import json
import os
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from announce import (Blocked, Event, Journal, event_url, export_receipt, import_receipts,
                      instance_lock, posting_sunday, require_window, run, validate_event,
                      assert_cloud_ready, configured_paths, state_directory, workflow_sunday)

URL = "https://www.meetup.com/geneve-football-friendly/events/316836775/"
DAY = date(2026, 10, 24)
BODY = "Football at Campus des Nations. 11:00–12:00 (arrive 10:45 am)"


def fixture():
    return {"@type": "Event", "url": URL, "startDate": "2026-10-24T10:45:00+02:00",
            "endDate": "2026-10-24T12:00:00+02:00", "eventStatus": "https://schema.org/EventScheduled",
            "location": {"name": "Ecole Internationale de Genève, Campus des Nation"},
            "description": BODY}


class TimeTests(unittest.TestCase):
    def test_sunday_and_cutoff(self):
        for value in ["2026-10-18T08:59:59+00:00", "2026-10-18T22:00:00+00:00",
                      "2026-10-17T11:00:00+02:00"]:
            with self.subTest(value=value), self.assertRaises(Blocked):
                posting_sunday(datetime.fromisoformat(value), sending=True)
        for value in ["2026-10-18T09:00:00+00:00", "2026-10-18T21:59:59+00:00"]:
            self.assertEqual(posting_sunday(datetime.fromisoformat(value), sending=True), date(2026, 10, 18))

    def test_dst_changes(self):
        for before, due in [("2026-10-25T09:59:59+00:00", "2026-10-25T10:00:00+00:00"),
                            ("2027-03-28T08:59:59+00:00", "2027-03-28T09:00:00+00:00")]:
            with self.assertRaises(Blocked):
                posting_sunday(datetime.fromisoformat(before), sending=True)
            posting_sunday(datetime.fromisoformat(due), sending=True)

    def test_preview_targets_next_sunday(self):
        self.assertEqual(posting_sunday(datetime.fromisoformat("2026-10-17T23:00:00+03:00")), date(2026, 10, 18))

    def test_stale_run_never_sends_next_week(self):
        with self.assertRaises(Blocked):
            require_window(date(2026, 10, 18), datetime.fromisoformat("2026-10-25T11:01:00+01:00"))

    def test_naive_clock_rejected(self):
        with self.assertRaises(Blocked):
            posting_sunday(datetime(2026, 10, 18, 11))


class EventTests(unittest.TestCase):
    def test_valid_event_and_exact_caption(self):
        event = validate_event(fixture(), URL, DAY, BODY)
        self.assertEqual(event.caption, "Link for registrations: " + URL)

    def test_canonical_urls(self):
        self.assertEqual(event_url(URL + "?eventOrigin=group_events_list"), URL)
        self.assertTrue(event_url("https://www.meetup.com/geneve-football-friendly/events/lqkpbvyjcnbpc/").endswith("lqkpbvyjcnbpc/"))
        for url in ["https://www.meetup.com/other/events/123/", "https://evil.test/", URL.replace("https:", "http:")]:
            with self.assertRaises(Blocked):
                event_url(url)

    def test_conflicts_fail_closed(self):
        changes = [{"eventStatus": "https://schema.org/EventCancelled"}, {"eventStatus": ""},
                   {"location": {"name": "Other venue"}}, {"endDate": "2026-10-24T13:00:00+02:00"},
                   {"startDate": "2026-10-22T10:45:00+02:00"}, {"startDate": "2026-10-24T10:45:00"},
                   {"description": "Arrive 10:45, unrelated game"}, {"url": URL.replace("316836775", "999") }]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(Blocked):
                validate_event(fixture() | change, URL, DAY, BODY)

    def test_visible_cancellation_overrides_metadata(self):
        with self.assertRaises(Blocked):
            validate_event(fixture(), URL, DAY, BODY + " Event is cancelled")


class ReceiptTests(unittest.TestCase):
    def test_preserve_shared_receipts_and_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sent.json"
            event = Event(URL, DAY)
            export_receipt(path, event, "sent")
            export_receipt(path, Event(URL.replace("316836775", "123"), DAY), "sent")
            self.assertEqual(len(json.loads(path.read_text())), 2)
            journal = Journal(Path(folder) / "db.sqlite")
            try:
                import_receipts(journal, [path])
                self.assertEqual(journal.status(URL), "existing")
            finally:
                journal.close()

    def test_uncertain_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.sqlite"
            journal = Journal(path)
            journal.record(Event(URL, DAY), "uncertain", "click may have happened")
            journal.close()
            journal = Journal(path)
            try:
                self.assertEqual(journal.status(URL), "uncertain")
            finally:
                journal.close()

    def test_lock_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            with instance_lock(path):
                with self.assertRaises(Blocked):
                    with instance_lock(path):
                        pass
            with instance_lock(path):
                pass

    def test_uncertain_run_cannot_click_send(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "photo.png"
            photo.write_bytes(b"test")
            journal = Journal(Path(folder) / "db.sqlite")
            journal.record(Event(URL, DAY), "uncertain", "previous attempt")
            cfg = {"photo": str(photo), "ui_verified": True,
                   "verified_invite": "https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX",
                   "selectors": {"test": "configured"}}
            try:
                with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                     patch("announce.local_now", return_value=datetime.fromisoformat("2026-10-18T11:00:00+02:00")), \
                     patch("announce.discover", return_value=Event(URL, DAY)), \
                     patch("announce.open_chat"), patch("announce.scan_duplicates", return_value=False), \
                     patch("announce.send_once") as send:
                    with self.assertRaisesRegex(Blocked, "uncertain"):
                        run(Mock(), cfg, journal, Path(folder), True)
                    send.assert_not_called()
            finally:
                journal.close()


class CloudTests(unittest.TestCase):
    def test_workflow_origin_and_dst(self):
        for origin, now in [("2026-10-18T09:00:00Z", "2026-10-18T11:30:00+02:00"),
                            ("2026-10-25T10:00:00Z", "2026-10-25T11:30:00+01:00")]:
            self.assertEqual(workflow_sunday(origin, datetime.fromisoformat(now)).weekday(), 6)

    def test_old_workflow_cannot_send_next_sunday(self):
        with self.assertRaises(Blocked):
            workflow_sunday("2026-10-18T09:00:00Z", datetime.fromisoformat("2026-10-25T11:30:00+01:00"))

    def test_monday_job_is_not_a_new_event(self):
        with self.assertRaises(Blocked):
            workflow_sunday("2026-10-19T09:00:00Z", datetime.fromisoformat("2026-10-19T11:30:00+02:00"))

    def test_missing_origin_is_blocked(self):
        with self.assertRaises(Blocked):
            workflow_sunday("", datetime.fromisoformat("2026-10-18T11:30:00+02:00"))

    def test_state_must_be_persistent_and_external(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            workspace = base / "checkout"
            workspace.mkdir()
            state = base / "persistent"
            state.mkdir()
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": str(workspace),
                                         "SUNDAY_STATE_DIR": str(state)}):
                self.assertEqual(state_directory(), state.resolve())
                for wrong in ["", "relative/path", str(workspace / "state"), str(base / "missing")]:
                    with self.subTest(wrong=wrong), patch.dict(os.environ, {"SUNDAY_STATE_DIR": wrong}), self.assertRaises(Blocked):
                        state_directory()

    def test_missing_profile_or_receipts_blocks_send(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            state = Path(folder)
            with self.assertRaises(Blocked):
                assert_cloud_ready(state)
            (state / "browser-profile").mkdir()
            (state / "receipts.sqlite3").touch()
            with self.assertRaises(Blocked):
                assert_cloud_ready(state)
            (state / "sent-events.json").write_text("[]")
            assert_cloud_ready(state)

    def test_config_paths_resolve_beside_config_not_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"photo": "session-photo.png", "receipt_journals": ["sent-events.json"]}))
            cfg = configured_paths(path)
            self.assertEqual(cfg["photo"], str(path.parent / "session-photo.png"))
            self.assertEqual(cfg["receipt_journals"], [str(path.parent / "sent-events.json")])

    @unittest.skipIf(os.name == "nt", "Linux-specific configuration check")
    def test_windows_paths_rejected_on_linux(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"photo": "C:/Users/example/photo.png", "receipt_journals": ["sent.json"]}))
            with self.assertRaises(Blocked):
                configured_paths(path)


if __name__ == "__main__":
    unittest.main()
