import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from core import (Blocked, CheckpointFailed, checkpoint, target_date, choose,
                  perform, validate_cancelled, verified_reply_ids)
from messages import CANCELLATION, VENUE


def run(args):
    from cryptography.fernet import Fernet
    from playwright.sync_api import sync_playwright
    from browser import Meetup, WhatsApp
    from storage import Store
    from gmail import Gmail
    cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    date = target_date(now, cfg, args.event_date, args.allow_late)
    if date is None:
        return {'result': 'skipped'}
    live = args.live
    if live and os.getenv('LIVE_ENABLED') != 'true':
        raise Blocked('LIVE_ENABLED is not true; complete dry-run validation first')
    if live and os.getenv('UI_VALIDATED') != 'true':
        raise Blocked('UI_VALIDATED is not true; selectors and session portability require live-account testing')
    store = Store(live)
    auth = json.loads(Fernet(os.environ['SESSION_KEY'].encode()).decrypt(Path('sessions.enc').read_bytes()))
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=auth, locale='en-US', timezone_id='Europe/Zurich')
        ctx.set_default_timeout(30000)
        meetup = Meetup(ctx, cfg)
        event = meetup.find(date)
        previous = store.data.get(event['id'], {})
        validate_cancelled(event, previous)
        count = previous.get('count') if previous.get('branch') == 'cancel' and event['cancelled'] else meetup.count()
        decision = choose(previous, count)
        report = {'date': str(date), 'event': event['url'], 'count': count,
                  'branch': decision, 'dry_run': not live}
        if not live:
            # Read-only service preflight. No state, RSVP, comments or messages are changed.
            if decision == 'cancel':
                Gmail(cfg).preflight(now.astimezone(ZoneInfo('Europe/Zurich')).date())
            if decision != 'nothing':
                wa = WhatsApp(ctx, cfg)
                wa.open()
                if decision == 'cancel':
                    report['reply_targets'] = wa.targets(event)
            browser.close()
            return report
        record = store.data.setdefault(event['id'], {})
        record.update({'date': str(date), 'url': event['url'], 'count': previous.get('count', count) if previous.get('branch') in ('cancel', 'confirm') else count,
                       'branch': decision, 'checked_at': now.isoformat()})
        checkpoint(store)
        if decision == 'nothing':
            browser.close()
            return report

        def step(key, fn):
            try:
                perform(store, record, key, fn)
            except CheckpointFailed:
                raise
            except Exception as exc:
                # Do not expose raw HTTP/browser exceptions (may contain secrets/PII).
                errors.append(f'{key}: {str(exc) if isinstance(exc, Blocked) else type(exc).__name__}')

        if decision == 'cancel':
            if record.get('steps', {}).get('gmail_reply', {}).get('status') != 'done':
                mail = Gmail(cfg)  # Verify account/thread before a new cancellation.
                mail.preflight(now.astimezone(ZoneInfo('Europe/Zurich')).date())
            step('meetup_cancel', meetup.cancel)
            if record.get('steps', {}).get('meetup_cancel', {}).get('status') != 'done':
                raise Blocked('Cancellation unverified; subsequent notifications withheld')
            if record.get('steps', {}).get('gmail_reply', {}).get('status') != 'done':
                step('gmail_reply', lambda: mail.send(event['id']))
            step('meetup_private_comment', lambda: meetup.comment(CANCELLATION, private=True))
        try:
            wa = WhatsApp(ctx, cfg)
            wa.open()
            wa.verified_this_run = verified_reply_ids(record)
            if decision == 'cancel':
                # Persist the original targets before sending so later retries cannot shift anchors.
                if 'reply_targets' not in record:
                    record['reply_targets'] = wa.targets(event)
                    checkpoint(store)
                for index, anchor in enumerate(record['reply_targets']):
                    step(f'whatsapp_reply_{index}', lambda a=anchor: wa.reply(a, CANCELLATION))
            else:
                for path in ('assets/venue-entrance.jpeg', 'assets/venue-pitch.jpeg'):
                    if not Path(path).is_file():
                        raise Blocked('Missing venue photo')
                step('whatsapp_confirmation_text', lambda: wa.text(VENUE))
                for label, path in (('entrance', 'assets/venue-entrance.jpeg'), ('pitch', 'assets/venue-pitch.jpeg')):
                    step('whatsapp_photo_' + label, lambda p=path: wa.photo(Path(p)))
        except CheckpointFailed:
            raise
        except Exception as exc:
            errors.append(f'WhatsApp: {str(exc) if isinstance(exc, Blocked) else type(exc).__name__}')
        if decision == 'confirm':
            step('meetup_confirmation_comment', lambda: meetup.comment(VENUE, private=False))
        browser.close()
        report['steps'] = record.get('steps', {})
        report['blockers'] = errors
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--event-date', help='Manual confirmation of following Saturday, YYYY-MM-DD')
    parser.add_argument('--allow-late', action='store_true')
    args = parser.parse_args()
    try:
        report = run(args)
    except Exception as exc:
        report = {'blockers': [str(exc) if isinstance(exc, Blocked) else
                              f'{type(exc).__name__}: configuration/service failure; inspect locally']}
    Path('report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    summary = json.dumps(report, indent=2)
    print(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write('### Football session decision\n```json\n' + summary + '\n```\n')
    raise SystemExit(1 if report.get('blockers') else 0)
