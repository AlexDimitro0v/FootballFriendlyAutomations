"""Sunday 11:00 Europe/Zurich photo announcement. Default: read-only dry run."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as daytime, timedelta
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
GENEVA = ZoneInfo("Europe/Zurich")
GROUP = "https://www.meetup.com/geneve-football-friendly/"
INVITE = "https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX"
LOG = logging.getLogger("sunday")


class Blocked(RuntimeError):
    pass


def local_now():
    print(datetime.now(GENEVA))
    return datetime.now(GENEVA)


def localize(now):
    if now.tzinfo is None:
        raise Blocked("A timezone-aware clock is required")
    return now.astimezone(GENEVA)


def posting_sunday(now, *, sending=False):
    now = localize(now)
    if sending and (now.weekday() != 6 or now.time() < daytime(11)):
        raise Blocked("Posting allowed only Sunday 11:00–23:59 Europe/Zurich; no catch-up")
    return now.date() + timedelta(days=(6 - now.weekday()) % 7)


def require_window(sunday, now=None):
    if posting_sunday(now or local_now(), sending=True) != sunday:
        raise Blocked("The scheduled Sunday's posting window has ended")


def event_url(url):
    """Canonicalize only a real link supplied by the site, including recurring IDs."""
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"meetup.com", "www.meetup.com"}:
        raise Blocked("Event URL is not on Meetup")
    match = re.fullmatch(r"/geneve-football-friendly/events/([A-Za-z0-9_-]+)/?", p.path)
    if not match:
        raise Blocked("Event belongs to another group or is not a detail URL")
    return GROUP + "events/" + match[1] + "/"


@dataclass(frozen=True)
class Event:
    url: str
    day: date

    @property
    def id(self):
        return self.url.rstrip("/").split("/")[-1]

    @property
    def caption(self):
        return f"Link for registrations: {self.url}"


def walk_events(value):
    if isinstance(value, list):
        for item in value:
            yield from walk_events(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if "Event" in ([types] if isinstance(types, str) else types):
            yield value
        for item in value.values():
            yield from walk_events(item)


def timestamp(value):
    try:
        return localize(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise Blocked("Event timestamp missing or invalid") from exc


def validate_event(data, opened_url, saturday, visible_text):
    url = event_url(opened_url)
    if event_url(data.get("url", "")) != url:
        raise Blocked("Page metadata does not identify the opened event")
    start, end = timestamp(data.get("startDate")), timestamp(data.get("endDate"))
    if start.date() != saturday or end.date() != saturday or saturday.weekday() != 5:
        raise Blocked("Event is not the intended Saturday")
    if data.get("eventStatus", "").rsplit("/", 1)[-1] != "EventScheduled":
        raise Blocked("Event is cancelled, rescheduled, or active status is unverified")
    if re.search(r"\b(?:event|session) (?:has been |is )?cancel[le]+d\b", visible_text, re.I):
        raise Blocked("Event page reports cancellation")
    location = data.get("location", {})
    venue = str(location.get("name", "")) if isinstance(location, dict) else ""
    if not re.search(r"campus des nations?\b", venue, re.I):
        raise Blocked("Event venue conflicts with the poster")
    if start.time() not in {daytime(10, 45), daytime(11)} or end.time() != daytime(12):
        raise Blocked("Event time conflicts with the poster")
    description = html.unescape(re.sub(r"<[^>]*>", " ", data.get("description", "")))
    play = r"11[:.]00\s*[–—-]\s*12[:.]00"
    arrival = r"(?:arriv\w*|be there)[^\n.]{0,50}10[:.]45"
    # Validate the event description itself, not unrelated recommendations in the page.
    if not re.search(play, description, re.I) or not re.search(arrival, description, re.I):
        raise Blocked("Description must confirm play 11:00–12:00 and arrival 10:45")
    if not re.search(play, visible_text, re.I) or not re.search(arrival, visible_text, re.I):
        raise Blocked("Visible event details do not corroborate the poster")
    return Event(url, saturday)


def detail(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.locator('script[type="application/ld+json"]').first.wait_for(state="attached")
    records = []
    for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            records.extend(walk_events(json.loads(raw)))
        except ValueError:
            continue
    actual = event_url(page.url)
    matches = []
    for record in records:
        try:
            if event_url(record.get("url", "")) == actual:
                matches.append(record)
        except Blocked:
            continue
    # Identical duplicate JSON-LD blocks are harmless; conflicting blocks are not.
    unique = {json.dumps(x, sort_keys=True): x for x in matches}
    if len(unique) != 1:
        raise Blocked(f"Unverified or ambiguous metadata on {actual}")
    return next(iter(unique.values())), actual, page.locator("body").inner_text()


def discover(page, saturday):
    page.goto(GROUP + "events/", wait_until="domcontentloaded")
    links = page.locator('a[href*="/geneve-football-friendly/events/"]')
    links.first.wait_for(state="attached")
    candidates = set()
    for href in links.evaluate_all("els => els.map(e => e.href)"):
        try:
            candidates.add(event_url(href))
        except Blocked:
            pass
    if not candidates:
        raise Blocked("No live event links found; inspect Meetup's upcoming-events UI")
    matches = []
    for url in sorted(candidates):
        data, actual, body = detail(page, url)
        if timestamp(data.get("startDate")).date() == saturday:
            matches.append(validate_event(data, actual, saturday, body))
    unique = {event.url: event for event in matches}
    if len(unique) != 1:
        raise Blocked(f"Expected one Saturday session; found {len(unique)}")
    return next(iter(unique.values()))


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (url TEXT PRIMARY KEY, day TEXT, status TEXT, at TEXT, evidence TEXT)")

    def status(self, url):
        row = self.db.execute("SELECT status FROM events WHERE url=?", (url,)).fetchone()
        return row[0] if row else None

    def record(self, event, status, evidence):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)",
                            (event.url, str(event.day), status, local_now().isoformat(), evidence))

    def close(self):
        self.db.close()


def import_receipts(journal, paths):
    for path in paths:
        if not path.exists():
            continue
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(records, list):
            raise Blocked(f"Unexpected receipt journal format: {path}")
        for row in records:
            url = row.get("event_url") or row.get("url")
            if not url:
                raise Blocked(f"Unidentified receipt in {path}; review before sending")
            url = event_url(url)
            if journal.status(url):
                continue
            event = Event(url, date.fromisoformat(row["event_date"]))
            outcome = row.get("outcome", row.get("status"))
            confirmed = outcome in {"sent", "delivered", "existing_sent_verified_duplicate_skipped", "duplicate_link_observed"}
            journal.record(event, "existing" if confirmed else "uncertain", f"Imported {path}: {outcome}")


def export_receipt(path, event, outcome):
    """Preserve the Codex journal and replace it atomically; only one scheduler may run."""
    rows = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else []
    if not isinstance(rows, list):
        raise Blocked("Shared receipt journal must contain a JSON list")
    now = local_now().isoformat()
    rows.append({"event_id": event.id, "event_url": event.url, "event_date": str(event.day),
                 "sent_timestamp": now if outcome == "sent" else None,
                 "verified_at": now, "outcome": outcome, "source": "sunday_announcement_python"})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def instance_lock(path):
    """OS lock is released even after a crash; prevents two copies sharing a profile."""
    with path.open("a+b") as handle:
        try:
            # Inspect size without reading another process's locked byte on Windows.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Blocked("Another Sunday script instance is running") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def unique(locator):
    locator.wait_for(state="visible", timeout=30000)
    if locator.count() != 1:
        raise Blocked("UI control is ambiguous; verify selectors")
    return locator


def assert_destination(page, cfg):
    header = unique(page.locator(cfg["selectors"]["group_header"])).inner_text()
    if cfg["whatsapp_group_name"] not in header or "Only admins can send messages" not in header:
        raise Blocked("Main announcement group header could not be verified")
    if page.locator(cfg["selectors"]["reply_context"]).count():
        raise Blocked("Exit quoted-reply mode before running")


def open_chat(page, cfg):
    page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded")
    search = page.get_by_role("textbox", name="Search or start a new chat", exact=True)
    unique(search).fill(cfg["whatsapp_group_name"])
    # A same-name community or subgroup causes a blocker, never first-match selection.
    unique(page.get_by_title(cfg["whatsapp_group_name"], exact=True)).click()
    assert_destination(page, cfg)
    composer = unique(page.locator(cfg["selectors"]["main_composer"]))
    if composer.inner_text().strip():
        raise Blocked("Existing message draft present; leave it untouched")


def scan_duplicates(page, context, cfg, event):
    """Conservatively block on any loaded outgoing link to the same event."""
    messages = page.locator(cfg["selectors"]["outgoing_messages"])
    resolver = None
    try:
        for href in messages.locator("a[href]").evaluate_all("els => els.map(e => e.href)"):
            parsed = urlsplit(href)
            if parsed.hostname == "meetu.ps":
                if resolver is None:
                    resolver = context.new_page()
                resolver.goto(href, wait_until="domcontentloaded")
                href = resolver.url
                if urlsplit(href).hostname not in {"meetup.com", "www.meetup.com"}:
                    raise Blocked("Cannot resolve an existing Meetup short link; inspect chat")
            try:
                if event_url(href) == event.url:
                    return True
            except Blocked:
                continue
        return False
    finally:
        if resolver:
            resolver.close()


def send_once(page, event_page, cfg, event, sunday, photo, journal, state):
    from playwright.sync_api import expect
    s = cfg["selectors"]
    assert_destination(page, cfg)
    require_window(sunday)
    unique(page.locator(s["attach_button"])).click()
    with page.expect_file_chooser() as chooser:
        unique(page.locator(s["photos_button"])).click()
    chooser.value.set_files(str(photo))
    # A photo preview, rather than a document preview, is required.
    unique(page.locator(s["preview_photo"]))
    editor = unique(page.locator(s["caption_editor"]))
    editor.fill(event.caption)
    expect(editor).to_have_text(event.caption)
    destination = unique(page.locator(s["preview_destination"]))
    expect(destination).to_have_text(cfg["whatsapp_group_name"])
    page.screenshot(path=str(state / f"preview-{event.id}.png"))
    # Refresh the actual event immediately before the irreversible click.
    data, url, body = detail(event_page, event.url)
    validate_event(data, url, event.day, body)
    button = unique(page.locator(s["send_photo_button"]))
    if not button.is_enabled():
        raise Blocked("Photo send control is disabled")
    evidence = f"intent; photo SHA256={hashlib.sha256(photo.read_bytes()).hexdigest()}"
    journal.record(event, "uncertain", evidence)
    require_window(sunday)  # Recheck after uploads/network waits and journaling.
    button.click(timeout=5000)
    # A queued clock or a caption still in the editor is NOT proof of sending.
    message = page.locator(s["outgoing_messages"]).filter(has_text=event.caption)
    expect(message).to_have_count(1, timeout=60000)
    expect(message.locator(s["photo_in_message"])).to_be_visible()
    expect(message.locator(s["sent_in_message"])).to_be_visible(timeout=60000)
    journal.record(event, "sent", "Outgoing photo, exact caption and Sent/Delivered indicator verified")
    try:
        page.screenshot(path=str(state / f"sent-{event.id}.png"))
    except Exception:
        LOG.warning("Message verified sent, but saving screenshot failed", exc_info=True)


def run(context, cfg, journal, state, send):
    sunday = posting_sunday(local_now(), sending=send)
    meetup = context.new_page()
    wa = None
    try:
        event = discover(meetup, sunday + timedelta(days=6))
        status = journal.status(event.url)
        if status in {"sent", "existing"}:
            LOG.info("DUPLICATE: already recorded for %s", event.url)
            return "duplicate"
        photo = (ROOT / cfg["photo"]).resolve()
        if not photo.is_file():
            raise Blocked(f"Poster unavailable: {photo}")
        if not send:
            LOG.info("DRY RUN: %s\nPhoto: %s\nCaption: %s\nReceipt status: %s",
                     event.day, photo, event.caption, status)
            return "preview"
        if not cfg["ui_verified"] or cfg["verified_invite"] != INVITE:
            raise Blocked("Complete UI/destination verification in config.json before --send")
        if any(not value for value in cfg["selectors"].values()):
            raise Blocked("Configure all WhatsApp selectors before --send")
        wa = context.new_page()
        open_chat(wa, cfg)
        if scan_duplicates(wa, context, cfg, event):
            journal.record(event, "existing", "Matching outgoing event link observed; do not resend")
            export_receipt((ROOT / cfg["receipt_journals"][0]).resolve(), event,
                           "duplicate_link_observed")
            LOG.info("DUPLICATE: matching outgoing event link; skipped")
            return "duplicate"
        if status == "uncertain":
            raise Blocked("Prior send outcome uncertain. Inspect full chat history; no automatic retry")
        send_once(wa, meetup, cfg, event, sunday, photo, journal, state)
        LOG.info("SENT: verified photo announcement for %s (%s)", event.day, event.url)
        try:
            export_receipt((ROOT / cfg["receipt_journals"][0]).resolve(), event, "sent")
        except Exception as exc:
            raise Blocked("Photo was VERIFIED SENT and saved in SQLite, but shared JSON receipt failed. Do not resend") from exc
        return "sent"
    finally:
        if wa:
            wa.close()
        meetup.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "run", "schedule"])
    parser.add_argument("--send", action="store_true", help="Enable real sending within Sunday window")
    parser.add_argument("--inspect", action="store_true", help="Open Playwright Inspector during login")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(state / "run.log", encoding="utf-8")])
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        with instance_lock(state / "instance.lock"):
            journal = Journal(state / "receipts.sqlite3")
            try:
                import_receipts(journal, [(ROOT / p).resolve() for p in cfg["receipt_journals"]])
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    context = pw.chromium.launch_persistent_context(str(state / "browser-profile"),
                                headless=args.headless, timezone_id="Europe/Zurich", locale="en-GB")
                    context.set_default_timeout(30000)
                    try:
                        if args.command == "login":
                            if args.headless:
                                raise Blocked("Login requires a visible browser")
                            page = context.new_page()
                            page.goto("https://web.whatsapp.com/")
                            context.new_page().goto(GROUP)
                            print(f"Link WhatsApp, verify the main announcement group against {INVITE}.")
                            if args.inspect:
                                page.pause()
                            input("Finish session/selector setup, then press Enter to close and save login: ")
                        elif args.command == "run":
                            run(context, cfg, journal, state, args.send)
                        else:
                            LOG.info("Waiting for Sunday 11:00 Europe/Zurich; send=%s", args.send)
                            attempted = None
                            while True:
                                now = local_now()
                                if now.weekday() == 6 and now.hour >= 11 and attempted != now.date():
                                    attempted = now.date()
                                    try:
                                        # Re-import to detect receipts written since this process started.
                                        import_receipts(journal, [(ROOT / p).resolve() for p in cfg["receipt_journals"]])
                                        run(context, cfg, journal, state, args.send)
                                    except Exception:
                                        LOG.exception("BLOCKED: inspect before manually retrying this Sunday")
                                time.sleep(15)
                    finally:
                        context.close()
            finally:
                journal.close()
    except KeyboardInterrupt:
        return 130
    except Exception:
        LOG.exception("BLOCKED: no success claimed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
