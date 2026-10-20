"""Decision rules and durable per-action checkpointing; no service dependencies."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo


class Blocked(RuntimeError):
    pass


class CheckpointFailed(Blocked):
    """Fatal: no further external actions are safe after a failed state write."""


def checkpoint(store):
    try:
        store.save()
    except Exception as exc:
        raise CheckpointFailed('State checkpoint failed; stop without further external actions') from exc


def validate_cancelled(event, previous):
    if event['cancelled'] and not (
        previous.get('branch') == 'cancel'
        and previous.get('steps', {}).get('meetup_cancel', {}).get('status') == 'done'
    ):
        raise Blocked('Event cancellation is external or uncertain; reconcile before continuing')


def verified_reply_ids(record):
    return {
        step['evidence']['message_id']
        for key, step in record.get('steps', {}).items()
        if key.startswith('whatsapp_reply_') and step.get('status') == 'done'
        and step.get('evidence', {}).get('message_id')
    }


def branch(count):
    if type(count) is not int or count < 0:
        raise Blocked('Attendee count is not a verified nonnegative integer')
    return 'cancel' if count <= 6 else 'nothing' if count <= 9 else 'confirm'


def target_date(now, config, manual_date=None, allow_late=False):
    if now.tzinfo is None or now.utcoffset() is None:
        raise Blocked('Current time must include a timezone')
    now = now.astimezone(ZoneInfo('Europe/Zurich'))
    if now.date().isoformat() in config.get('skip_dates', []):
        return None
    if now.weekday() != 4:
        raise Blocked('Only Friday runs are allowed; never reinterpret tomorrow on another day')
    if now.time() < time(10, 50):
        raise Blocked('Too early: decision time is 10:50 Europe/Zurich')
    if now.time() >= time.fromisoformat(config['latest_start']) and not allow_late:
        raise Blocked('Late start: use manual dispatch with allow_late after reviewing venue deadline')
    result = now.date() + timedelta(days=1)
    if manual_date and manual_date != result.isoformat():
        raise Blocked('Manual event date must equal the following Saturday')
    return result


def choose(existing, count):
    # A no-action observation does not lock a future explicitly requested rerun.
    if existing.get('branch') in ('cancel', 'confirm'):
        return existing['branch']
    return branch(count)


def perform(store, event, key, action):
    steps = event.setdefault('steps', {})
    if steps.get(key, {}).get('status') == 'done':
        return
    if key in steps:
        raise Blocked(f'{key}: earlier result uncertain; inspect the service and reconcile state before retrying')
    # Persist BEFORE the irreversible side effect. A crash must not cause a resend.
    steps[key] = {'status': 'pending', 'started': datetime.now().astimezone().isoformat()}
    checkpoint(store)
    evidence = action()
    if not evidence:
        raise Blocked(f'{key}: no verification evidence')
    steps[key] = {'status': 'done', 'evidence': evidence}
    checkpoint(store)
