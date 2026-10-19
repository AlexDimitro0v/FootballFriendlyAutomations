"""Thursday reminders on a persistent cloud runner. Default: read-only preview."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
GENEVA = ZoneInfo('Europe/Zurich')
UTC = timezone.utc
LOG = logging.getLogger('football')
PUBLIC = '''Friendly football this Saturday ⚽️🏆 5-or 6-a-side depending on numbers.

Please register on Meetup by Friday at 10:45 so we can count you in for Saturday’s game. We review registrations on Friday at 10:50 to notify the venue before 11:00. All times are Geneva local time.

We need at least 10 registered players to confirm the session, so please register early - even if you’ve already told us you’re coming!'''
WELCOME = ('Hi! 👋 I noticed yоu have joined the Geneva Football Friendly Group. '
           'We organize a friendly international game every Saturday from 10:45–12:00 '
           'at Campus des Nations in Geneva.\n\n'
           'Meetup link to register for the next event: {url}')

class Blocked(RuntimeError):
    pass

def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise Blocked('Timestamp requires a timezone')
    return result

def now_local():
    return datetime.now(GENEVA)

def scheduled_time(now, *, sending=False, origin=None):
    if now.tzinfo is None:
        raise Blocked('Clock requires a timezone')
    now = now.astimezone(GENEVA)
    target = now.replace(hour=11, minute=0, second=0, microsecond=0)
    if sending:
        if now.weekday() != 3 or now < target:
            raise Blocked('Send only Thursday 11:00–23:59 Geneva; no next-day catch-up')
        if origin and origin.astimezone(GENEVA).date() != now.date():
            raise Blocked('Stale workflow: original run was created on a different day')
    else:
        target += timedelta(days=(3 - now.weekday()) % 7)
    return target

def require_window(scheduled, origin=None):
    if scheduled_time(now_local(), sending=True, origin=origin) != scheduled:
        raise Blocked('Scheduled posting window ended')

def fallback_start(end):
    return end.astimezone(UTC) - timedelta(hours=168)

def eligible(joined, start, end):
    if any(x.tzinfo is None for x in (joined, start, end)):
        raise Blocked('Join timestamps must be timezone-aware')
    return start.timestamp() <= joined.timestamp() <= end.timestamp()

def state_directory():
    raw = os.environ.get('FOOTBALL_STATE_DIR')
    if not raw or not Path(raw).is_absolute():
        raise Blocked('Set FOOTBALL_STATE_DIR to an absolute persistent directory')
    state = Path(raw).resolve()
    if state == ROOT.parent or ROOT.parent in state.parents:
        raise Blocked('State must be outside the Git checkout')
    state.mkdir(parents=True, exist_ok=True)
    return state

@contextmanager
def instance_lock(path):
    with path.open('a+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Blocked('Another process is using this state directory') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS actions (
            key TEXT PRIMARY KEY, status TEXT NOT NULL, evidence TEXT, updated TEXT);
          CREATE TABLE IF NOT EXISTS checkpoints (platform TEXT PRIMARY KEY, through TEXT);
          CREATE TABLE IF NOT EXISTS pending (
            platform TEXT, member_id TEXT, data TEXT, PRIMARY KEY(platform, member_id));
        ''')

    def status(self, key):
        row = self.db.execute('SELECT status FROM actions WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def record(self, key, status, evidence=''):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO actions VALUES (?,?,?,?)',
                            (key, status, evidence, now_local().isoformat()))

    def start(self, platform, end):
        row = self.db.execute('SELECT through FROM checkpoints WHERE platform=?', (platform,)).fetchone()
        start = aware(row[0]) if row else fallback_start(end)
        if start > end:
            raise Blocked('Checkpoint is ahead of scheduled run')
        return start

    def queue(self, platform, member):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?)',
                            (platform, member['id'], json.dumps(member, ensure_ascii=False)))

    def pending(self, platform):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT data FROM pending WHERE platform=?', (platform,))]

    def remove_pending(self, platform, member_id):
        with self.db:
            self.db.execute('DELETE FROM pending WHERE platform=? AND member_id=?', (platform, member_id))

    def checkpoint(self, platform, through):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?)',
                            (platform, through.isoformat()))

    def import_codex(self, path):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        for run in data.get('runs', []):
            day = aware(run['scheduled_at']).astimezone(GENEVA).date().isoformat()
            for platform in ('whatsapp', 'meetup'):
                info = run.get(platform, {})
                key = f'{platform}:public:{day}'
                if info.get('public_post', '').startswith('verified_') and not self.status(key):
                    self.record(key, 'sent', 'Imported verified Codex public receipt')
                for member in info.get('recipients', []):
                    if member.get('outcome') == 'verified_sent':
                        self.record(f"{platform}:welcome:{member['id']}", 'sent',
                                    'Imported verified Codex welcome')
        # Legacy journals did not certify full coverage. Never infer checkpoints.

    def export(self, path):
        atomic_json(path, {
            'actions': [dict(zip(('key', 'status', 'evidence', 'updated'), r))
                        for r in self.db.execute('SELECT * FROM actions')],
            'checkpoints': dict(self.db.execute('SELECT * FROM checkpoints')),
            'pending': [json.loads(r[0]) for r in self.db.execute('SELECT data FROM pending')],
        })

def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

def deliver(adapter, journal, key, text, since, send, scheduled, origin=None):
    status = journal.status(key)
    if status == 'sent':
        return 'already_sent'
    if adapter.duplicate(text, since):
        if send:
            journal.record(key, 'sent', 'Matching live outgoing message verified')
        return 'already_sent'
    if status == 'uncertain':
        raise Blocked('Previous click outcome uncertain; inspect before resolving')
    adapter.prepare(text, fill=send)
    if not send:
        return 'preview'
    require_window(scheduled, origin)
    journal.record(key, 'uncertain', 'Intent persisted before final click')
    adapter.send(text, before_click=lambda: require_window(scheduled, origin))
    journal.record(key, 'sent', 'Exact outgoing text and platform sent indicator verified')
    return 'sent'

def run(context, cfg, journal, state, send, origin=None):
    from browser import Adapter, discover_event
    scheduled = scheduled_time(now_local(), sending=send, origin=origin)
    report = {'scheduled': scheduled.isoformat(), 'mode': 'send' if send else 'preview',
              'platforms': {}, 'event_url': None}
    event_page = context.new_page()
    try:
        event = discover_event(event_page, scheduled.date() + timedelta(days=2), cfg)
        report['event_url'] = event
        for platform in ('whatsapp', 'meetup'):
            result = {'public': 'blocked', 'welcomes_sent': 0, 'welcomes_existing': 0,
                      'welcomes_preview': 0, 'coverage_complete': False, 'blockers': []}
            report['platforms'][platform] = result
            adapter = Adapter(context.new_page(), cfg[platform], platform)
            try:
                try:
                    adapter.open_public()
                    result['public'] = deliver(adapter, journal,
                        f'{platform}:public:{scheduled.date()}', PUBLIC,
                        scheduled - timedelta(days=6), send, scheduled, origin)
                except Exception as exc:
                    LOG.exception('%s public blocked', platform)
                    result['blockers'].append(str(exc))
                start = journal.start(platform, scheduled)
                result['window_start'] = start.isoformat()
                members, complete, issues = [], False, []
                try:
                    members, complete, issues = adapter.scan_members(start, scheduled)
                except Exception as exc:
                    issues.append(str(exc))
                result['blockers'].extend(issues)
                result['coverage_complete'] = complete
                work = {m['id']: m for m in journal.pending(platform)}
                for member in members:
                    work[member['id']] = member
                    if send:
                        journal.queue(platform, member)
                # Persist every eligible recipient before advancing coverage.
                if send and complete:
                    journal.checkpoint(platform, scheduled)
                for member in work.values():
                    try:
                        key = f"{platform}:welcome:{member['id']}"
                        if journal.status(key) == 'sent':
                            outcome = 'already_sent'
                        else:
                            adapter.open_private(member)
                            outcome = deliver(adapter, journal, key, WELCOME.format(url=event),
                                              None, send, scheduled, origin)
                        counter = {'sent': 'welcomes_sent', 'already_sent': 'welcomes_existing',
                                   'preview': 'welcomes_preview'}[outcome]
                        result[counter] += 1
                        if send and outcome in ('sent', 'already_sent'):
                            journal.remove_pending(platform, member['id'])
                    except Exception as exc:
                        LOG.exception('%s private blocked', platform)
                        result['blockers'].append(f"Member {member['id']}: {exc}")
                try:
                    adapter.page.screenshot(path=str(state / f'{platform}-last.png'))
                except Exception:
                    LOG.warning('Could not save %s screenshot; receipts retained', platform)
            finally:
                adapter.page.close()
        return report
    finally:
        event_page.close()
        atomic_json(state / 'last-report.json', report)
        journal.export(state / 'receipts-export.json')

def write_summary(report):
    lines = [f"Thursday reminder: {report.get('mode', 'blocked')}",
             f"Scheduled: {report.get('scheduled', 'unavailable')}"]
    for platform, item in report.get('platforms', {}).items():
        lines.append(f"{platform}: public={item['public']}; welcomes sent={item['welcomes_sent']}; "
                     f"already welcomed={item['welcomes_existing']}; preview={item['welcomes_preview']}; "
                     f"coverage complete={item['coverage_complete']}; blockers={len(item['blockers'])}")
    output = '\n\n'.join(lines) + '\n'
    print(output)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(output)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('login', 'run', 'check-config', 'import-receipts'))
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    state = state_directory()
    logging.basicConfig(filename=state / 'run.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    report = {}
    try:
        with instance_lock(state / 'instance.lock'):
            journal = Journal(state / 'receipts.sqlite3')
            try:
                if args.command == 'import-receipts':
                    if not args.file:
                        raise Blocked('Provide --file with the privately copied Codex journal')
                    journal.import_codex(args.file)
                    journal.export(state / 'receipts-export.json')
                    print('Historical receipts imported; checkpoints unchanged.')
                    return 0
                cfg = json.loads((state / 'config.json').read_text(encoding='utf-8-sig'))
                from browser import validate_config
                if args.command == 'check-config':
                    validate_config(cfg)
                    print('Configuration present. Live UI verification is still required.')
                    return 0
                if args.command == 'run':
                    validate_config(cfg)
                    if args.send and (not cfg.get('sending_enabled') or
                                      not cfg.get('codex_thursday_disabled_confirmed')):
                        raise Blocked('Enable sending and confirm Codex Thursday automation is disabled')
                origin = aware(os.environ['FOOTBALL_RUN_CREATED_AT']) if os.environ.get('FOOTBALL_RUN_CREATED_AT') else None
                if args.send:
                    scheduled_time(now_local(), sending=True, origin=origin)
                from playwright.sync_api import sync_playwright
                with sync_playwright() as pw:
                    context = pw.chromium.launch_persistent_context(str(state / 'browser-profile'),
                        headless=args.headless, timezone_id='Europe/Zurich', locale='en-GB')
                    context.set_default_timeout(30000)
                    try:
                        if args.command == 'login':
                            if args.headless:
                                raise Blocked('Login needs a visible VM desktop browser')
                            page = context.new_page()
                            page.goto('https://web.whatsapp.com/')
                            context.new_page().goto('https://www.meetup.com/geneve-football-friendly/')
                            if args.inspect:
                                page.pause()
                            input('Link WhatsApp and sign in to Meetup; press Enter when finished: ')
                        else:
                            report = run(context, cfg, journal, state, args.send, origin)
                    finally:
                        context.close()
            finally:
                journal.db.close()
    except Exception:
        LOG.exception('Run blocked; inspect private log')
        print('Blocked. Inspect run.log in the private cloud state directory.')
        return 1
    if report:
        write_summary(report)
        return 1 if any(x['blockers'] or not x['coverage_complete']
                        for x in report['platforms'].values()) else 0
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
