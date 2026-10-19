"""Thursday football reminders. See README.md before enabling --send."""
import argparse
import csv
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
GENEVA = ZoneInfo("Europe/Zurich")
PUBLIC = "Spaces left for 5 or 6-sided game this Saturday. Come join!"
WELCOME = ("Hi! 👋 I noticed yоu have joined the Geneva Football Friendly Group. "
           "We organize a friendly international game every Saturday from 10:45–12:00 "
           "at Campus des Nations in Geneva.\n\n"
           "Meetup link to register for the next event: {url}")
LOG = logging.getLogger("football")


def scheduled_time(now):
    """Most recent Thursday at 20:00, using Geneva's DST rules."""
    target = now.replace(hour=20, minute=0, second=0, microsecond=0)
    target -= timedelta(days=(now.weekday() - 3) % 7)
    if target > now:
        target -= timedelta(days=7)
    return target


def eligible(joined, scheduled):
    # Timestamp comparison keeps the window exactly 96 elapsed hours across DST.
    if joined.tzinfo is None:
        raise ValueError("Join timestamps must include a UTC offset")
    return scheduled.timestamp() - 96 * 3600 <= joined.timestamp() <= scheduled.timestamp()


def walk_events(value):
    if isinstance(value, list):
        for item in value:
            yield from walk_events(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if "Event" in ([types] if isinstance(types, str) else types):
            yield value
        for child in value.values():
            if isinstance(child, (list, dict)):
                yield from walk_events(child)


def valid_meetup_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {"meetup.com", "www.meetup.com"}


def find_event(page, config, saturday):
    page.goto(config["group_url"], wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    links = page.locator('a[href*="/geneve-football-friendly/events/"]').evaluate_all(
        "els => els.map(e => e.href)")
    if config.get("event_url"):
        links.append(config["event_url"])
    candidates = set()
    for link in links:
        if valid_meetup_url(link) and re.search(r"/geneve-football-friendly/events/\d+", link):
            candidates.add(link.split("?")[0].rstrip("/") + "/")
    matches = {}
    for url in sorted(candidates):
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            for event in walk_events(data):
                start = event.get("startDate", "")
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt.tzinfo is None or dt.astimezone(GENEVA).date() != saturday:
                    continue
                status = event.get("eventStatus", "")
                if status and not status.endswith("EventScheduled"):
                    continue
                body = page.locator("body").inner_text()
                if "campus des nations" not in body.lower():
                    continue
                if not re.search(r"10[:.]45", body) or not re.search(r"12[:.]00", body):
                    continue
                if re.search(r"event (?:has been |is )?cancelled|event canceled", body, re.I):
                    continue
                matches[url] = event
    if len(matches) != 1:
        raise RuntimeError(f"Expected one verified Saturday event; found {len(matches)}. No messages sent.")
    return next(iter(matches))


def recipients(path, scheduled):
    found = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            platform = row["platform"].strip().lower()
            if platform not in {"whatsapp", "meetup"}:
                raise ValueError("Member platform must be whatsapp or meetup")
            if not row["member_id"] or not row["evidence"]:
                raise ValueError("Every member needs a stable identifier and join-date evidence")
            joined = datetime.fromisoformat(row["joined_at"].replace("Z", "+00:00"))
            url = urlsplit(row["message_url"])
            allowed = {"web.whatsapp.com"} if platform == "whatsapp" else {"www.meetup.com", "meetup.com"}
            if url.scheme != "https" or url.hostname not in allowed:
                raise ValueError("Private-message URL must belong to the corresponding platform")
            key = (platform, row["member_id"])
            if eligible(joined, scheduled) and key not in seen:
                row["platform"] = platform
                found.append(row)
                seen.add(key)
    return found


class Journal:
    def __init__(self):
        self.db = sqlite3.connect(ROOT / "receipts.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, status TEXT, at TEXT)")

    def status(self, key):
        row = self.db.execute("SELECT status FROM receipts WHERE id=?", (key,)).fetchone()
        return row[0] if row else None

    def record(self, key, status):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO receipts VALUES (?,?,?)",
                            (key, status, datetime.now(GENEVA).isoformat()))


def deliver(page, journal, key, text, editor_selector, button_selector, sent_selector, send):
    previous = journal.status(key)
    if previous:
        LOG.warning("Skipping %s: journal status %s", key, previous)
        return
    if not editor_selector or not button_selector or not sent_selector:
        raise RuntimeError("Messaging selectors are not configured; see README.md")
    editor = page.locator(editor_selector)
    editor.wait_for(state="visible", timeout=30000)
    if editor.count() != 1:
        raise RuntimeError("Message composer is ambiguous")
    sent = page.locator(sent_selector).filter(has_text=text)
    if sent.count():
        LOG.warning("Matching message already visible; skipping %s", key)
        return
    if not send:
        LOG.info("DRY RUN: %s\n%s", key, text)
        return
    editor.fill(text)
    button = page.locator(button_selector)
    if button.count() != 1 or not button.is_enabled():
        raise RuntimeError("Send button missing, ambiguous, or disabled; nothing sent")
    # Commit intent before clicking. Unknown outcomes are never blindly retried.
    journal.record(key, "uncertain")
    button.click()
    sent.first.wait_for(state="visible", timeout=30000)
    journal.record(key, "sent")
    LOG.info("Verified sent: %s", key)


def whatsapp_group(page, config):
    page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded")
    name = config["whatsapp_group_name"]
    group = page.get_by_title(name, exact=True)
    group.first.wait_for(state="visible", timeout=60000)
    if group.count() != 1:
        raise RuntimeError("WhatsApp group is ambiguous; select the main announcement group manually")
    group.click()
    page.locator("#main header").filter(has_text=name).wait_for(timeout=15000)
    # Refuse reply context rather than accidentally replying to an announcement.
    if page.locator('#main [data-testid="quoted-message"]').count():
        raise RuntimeError("Exit quoted-reply mode in WhatsApp before running")


def run(context, config, args):
    now = datetime.now(GENEVA)
    scheduled = scheduled_time(now)
    if args.send and (now.weekday() != 3 or now.hour < 20):
        raise RuntimeError("Sending is allowed only Thursday from 20:00 to midnight Geneva time")
    if not args.send and now < now.replace(hour=20, minute=0) and now.weekday() == 3:
        scheduled += timedelta(days=7)
    elif not args.send and now.weekday() != 3:
        scheduled += timedelta(days=7)
    saturday = scheduled.date() + timedelta(days=2)
    page = context.new_page()
    journal = Journal()
    try:
        event = find_event(page, config, saturday)
        LOG.info("Verified event: %s", event)
        members = recipients(ROOT / "members.csv", scheduled)
        LOG.warning("Membership coverage depends on members.csv; automatic join-date discovery is NOT implemented.")
        operations = [("whatsapp", "public", None), ("meetup", "public", None)]
        operations += [(m["platform"], "welcome", m) for m in members]
        for platform, kind, member in operations:
            key = f"{platform}:{kind}:" + (member["member_id"] if member else str(saturday))
            try:
                if journal.status(key):
                    LOG.info("Skipping recorded action %s", key)
                    continue
                text = WELCOME.format(url=event) if member else PUBLIC
                if member:
                    page.goto(member["message_url"], wait_until="domcontentloaded")
                elif platform == "whatsapp":
                    whatsapp_group(page, config)
                else:
                    page.goto(event, wait_until="domcontentloaded")
                if platform == "whatsapp":
                    deliver(page, journal, key, text,
                            '#main footer [contenteditable="true"][role="textbox"]',
                            '#main footer button:has([data-icon="send"])',
                            '#main .message-out', args.send)
                else:
                    prefix = "meetup_dm" if member else "meetup_comment"
                    deliver(page, journal, key, text, config[prefix + "_editor"],
                            config[prefix + "_send"], config["meetup_sent_message"], args.send)
            except Exception as error:
                LOG.error("BLOCKED %s: %s", key, error)
    finally:
        page.close()
        journal.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "run", "schedule"])
    parser.add_argument("--send", action="store_true", help="Actually send; default is dry run")
    parser.add_argument("--inspect", action="store_true", help="Open Playwright Inspector for selector setup")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "reminder.log", encoding="utf-8")])
    from playwright.sync_api import sync_playwright
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(str(ROOT / "firefox-profile"), headless=False)
        try:
            if args.command == "login":
                page = context.new_page()
                page.goto("https://web.whatsapp.com/")
                meetup = context.new_page()
                meetup.goto(config["group_url"])
                if args.inspect:
                    meetup.pause()
                input("Sign into both sites, verify your main announcement group, then press Enter here to save and close. ")
            elif args.command == "run":
                run(context, config, args)
            else:
                LOG.info("Waiting for Thursdays at 20:00 Europe/Zurich. Keep this process running.")
                attempted = None
                while True:
                    now = datetime.now(GENEVA)
                    if now.weekday() == 3 and now.hour >= 20 and attempted != now.date():
                        attempted = now.date()
                        try:
                            run(context, config, args)
                        except Exception:
                            LOG.exception("Run blocked")
                    time.sleep(15)
        finally:
            context.close()


if __name__ == "__main__":
    main()
