"""Playwright UI adapter. Account-specific selectors must be calibrated on the VM.

No private APIs, guessed recipient IDs, CAPTCHA bypass, or automatic legal acceptance.
Collection termination requires a verified end marker or dated coverage boundary.
"""
from datetime import datetime, time, timedelta
import html
import json
import re
from urllib.parse import urljoin, urlsplit

from reminder import Blocked, GENEVA, PUBLIC, WELCOME, aware, eligible

GROUP = 'https://www.meetup.com/geneve-football-friendly/'
INVITE = 'https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX'


def canonical_event(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ('meetup.com', 'www.meetup.com'):
        raise Blocked('Invalid Meetup event URL')
    if not re.fullmatch(r'/geneve-football-friendly/events/[\w-]+/?', p.path):
        raise Blocked('Event URL must identify this group and an individual event')
    return 'https://www.meetup.com' + p.path.rstrip('/') + '/'


def walk_events(value):
    if isinstance(value, list):
        for item in value:
            yield from walk_events(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if 'Event' in ([types] if isinstance(types, str) else types):
            yield value
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from walk_events(item)


def validate_event(data, url, saturday, body):
    if canonical_event(data.get('url', '')) != canonical_event(url):
        raise Blocked('Metadata does not identify the opened event')
    start = aware(data['startDate']).astimezone(GENEVA)
    end = aware(data['endDate']).astimezone(GENEVA)
    if saturday.weekday() != 5 or start.date() != saturday or end.date() != saturday:
        raise Blocked('Event is not the intended Saturday')
    if data.get('eventStatus', '').rsplit('/', 1)[-1] != 'EventScheduled':
        raise Blocked('Active event status could not be verified')
    if start.time() not in (time(10, 45), time(11)) or end.time() != time(12):
        raise Blocked('Event time conflicts with welcome')
    location = data.get('location', {})
    if not isinstance(location, dict) or not re.search(r'campus des nations?\b', location.get('name', ''), re.I):
        raise Blocked('Venue does not match Campus des Nations')
    description = html.unescape(re.sub('<[^>]*>', ' ', data.get('description', '')))
    for text in (description, body):
        if not re.search(r'11[:.]00\s*[–—-]\s*12[:.]00', text):
            raise Blocked('Cannot verify playing time in event details')
        if not re.search(r'(?:arriv\w*|be there)[^\n.]{0,50}10[:.]45', text, re.I):
            raise Blocked('Cannot verify arrival 10:45 in event details')
        if re.search(r'\b(?:event|session) (?:has been |is )?cancel[le]+d\b', text, re.I):
            raise Blocked('Cancellation notice found')
    return canonical_event(url)


def discover_event(page, saturday, cfg):
    page.goto(GROUP + 'events/', wait_until='domcontentloaded')
    links = page.locator('a[href*="/geneve-football-friendly/events/"]')
    links.first.wait_for(state='attached')
    urls = set()
    for href in links.evaluate_all('els => els.map(e => e.href)'):
        try:
            urls.add(canonical_event(href))
        except Blocked:
            pass
    if cfg.get('event_url'):
        urls.add(canonical_event(cfg['event_url']))
    matches = set()
    for url in sorted(urls):
        page.goto(url, wait_until='domcontentloaded')
        page.locator('script[type="application/ld+json"]').first.wait_for(state='attached')
        for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
            try:
                records = list(walk_events(json.loads(raw)))
            except ValueError:
                continue
            for event in records:
                try:
                    same = canonical_event(event.get('url', '')) == canonical_event(page.url)
                except Blocked:
                    same = False
                if same and aware(event['startDate']).astimezone(GENEVA).date() == saturday:
                    matches.add(validate_event(event, page.url, saturday, page.locator('body').inner_text()))
    if len(matches) != 1:
        raise Blocked(f'Expected one verified Saturday event; found {len(matches)}')
    return next(iter(matches))


def unique(locator):
    locator.wait_for(state='visible', timeout=30000)
    if locator.count() != 1:
        raise Blocked('Ambiguous UI control')
    return locator


def normalized(text):
    return ' '.join(text.split())


def joined_interval(value, spec):
    """Date-only joins represent an interval, never a guessed midnight timestamp."""
    match = re.search(spec.get('pattern', r'(.+)'), value)
    if not match:
        raise Blocked('No joining date in configured membership field')
    value = match.group(1)
    if spec.get('format') == 'iso':
        dt = aware(value)
        return dt, dt
    dt = datetime.strptime(value, spec['format']).replace(tzinfo=GENEVA)
    if spec.get('date_only', True):
        return dt, dt + timedelta(days=1) - timedelta(microseconds=1)
    raise Blocked('Exact membership times require ISO timestamps with an explicit offset')


def classify_interval(lo, hi, start, end):
    if hi.timestamp() < start.timestamp() or lo.timestamp() > end.timestamp():
        return 'outside'
    if eligible(lo, start, end) and eligible(hi, start, end):
        return 'inside'
    return 'ambiguous'


def field(row, spec):
    loc = row.locator(spec['selector']) if spec.get('selector') else row
    if loc.count() != 1:
        raise Blocked('Membership field is missing or ambiguous')
    value = loc.get_attribute(spec['attribute']) if spec.get('attribute') else loc.inner_text()
    if value is None:
        raise Blocked('Membership field attribute is absent')
    return value.strip()


def identity(raw, platform):
    if platform == 'whatsapp':
        digits = re.sub(r'[\s()\-]', '', raw)
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}', digits):
            raise Blocked('WhatsApp member needs a verified full phone number, not a display name')
        return digits
    match = re.fullmatch(r'(?:https://(?:www\.)?meetup.com/members/)?([0-9]+)/?', raw)
    if not match:
        raise Blocked('Meetup member needs a stable member ID or member-profile URL')
    return match.group(1)


def validate_config(cfg):
    if cfg.get('schema_version') != 2:
        raise Blocked('Use the version 2 config template')
    if cfg.get('verified_whatsapp_invite') != INVITE:
        raise Blocked('Verify the main WhatsApp announcement group against its invite URL')
    for platform in ('whatsapp', 'meetup'):
        p = cfg[platform]
        if not p.get('ui_verified'):
            raise Blocked(f'{platform}: UI selectors have not been verified on the cloud runner')
        for section in ('public', 'private'):
            c = p[section]
            for key in ('header', 'header_pattern', 'editor', 'send', 'messages', 'message_text', 'sent', 'history_end'):
                if not c.get(key):
                    raise Blocked(f'{platform}.{section}.{key} requires a verified selector/value')
            if not c.get('history_more'):
                raise Blocked(f'{platform}.{section}.history_more must navigate to older messages')
        m = p['members']
        for key in ('rows', 'ready', 'end', 'id', 'joined', 'advance'):
            if not m.get(key):
                raise Blocked(f'{platform}.members.{key} must be configured')


class Adapter:
    def __init__(self, page, cfg, platform):
        self.page, self.cfg, self.platform = page, cfg, platform
        self.mode = 'public'
        self.member = None

    def safe_goto(self, url):
        host = urlsplit(url).hostname
        allowed = ('web.whatsapp.com',) if self.platform == 'whatsapp' else ('www.meetup.com', 'meetup.com')
        if urlsplit(url).scheme != 'https' or host not in allowed:
            raise Blocked('Destination URL is outside this platform')
        self.page.goto(url, wait_until='domcontentloaded')
        if urlsplit(self.page.url).hostname not in allowed:
            raise Blocked('Unexpected navigation destination')

    def steps(self, steps, member=None):
        """Only calibrated navigation actions; not arbitrary page JS or executable config."""
        values = {'id': member['id']} if member else {}
        for step in steps:
            op = step['action']
            value = step.get('value', '')
            if member:
                value = value.replace('{id}', values['id'])
            if op == 'goto':
                self.safe_goto(value)
            elif op == 'click':
                unique(self.page.locator(step['selector'])).click()
            elif op == 'fill':
                unique(self.page.locator(step['selector'])).fill(value)
            elif op == 'press':
                unique(self.page.locator(step['selector'])).press(value)
            elif op == 'wait':
                unique(self.page.locator(step['selector']))
            elif op == 'scroll':
                loc = unique(self.page.locator(step['selector']))
                loc.hover()
                self.page.mouse.wheel(0, int(value))
                self.page.wait_for_timeout(700)
            else:
                raise Blocked(f'Unsupported navigation action: {op}')

    def assert_destination(self):
        c = self.cfg[self.mode]
        header = unique(self.page.locator(c['header'])).inner_text()
        pattern = c['header_pattern']
        if self.member:
            # A stable ID must be visible in the DM header or a configured identity attribute.
            raw = field(self.page.locator(c['header']), c.get('identity', {}))
            if identity(raw, self.platform) != self.member['id']:
                raise Blocked('Private conversation identity mismatch')
        elif not re.search(pattern, header):
            raise Blocked('General group destination could not be verified')
        if self.platform == 'meetup' and self.mode == 'public':
            if urlsplit(self.page.url).path.rstrip('/') != '/geneve-football-friendly/discussions':
                raise Blocked('Meetup public destination must be general group Discussions')
        if c.get('reply_context') and self.page.locator(c['reply_context']).count():
            raise Blocked('Reply/quote context present; refusing a thread reply')

    def open_public(self):
        self.mode, self.member = 'public', None
        self.steps(self.cfg['public']['open'])
        self.assert_destination()

    def open_private(self, member):
        self.mode, self.member = 'private', member
        self.steps(self.cfg['private']['open'], member)
        self.assert_destination()

    def scan_members(self, start, end):
        c = self.cfg['members']
        self.steps(c['open'])
        unique(self.page.locator(c['ready']))
        found, issues, seen, snapshots = {}, [], set(), set()
        reached_end = False
        for _ in range(c.get('max_pages', 100)):
            crossed_start = False
            rows = self.page.locator(c['rows'])
            signature = tuple(rows.all_text_contents())
            if signature in snapshots:
                issues.append('Membership navigation stalled; full coverage unverified')
                break
            snapshots.add(signature)
            for row in rows.all():
                evidence = row.inner_text()
                if evidence in seen:
                    continue
                seen.add(evidence)
                try:
                    lo, hi = joined_interval(field(row, c['joined']), c['joined'])
                    crossed_start |= hi.timestamp() < start.timestamp()
                    classification = classify_interval(lo, hi, start, end)
                    if classification == 'outside':
                        continue
                    member_id = identity(field(row, c['id']), self.platform)
                    if classification == 'ambiguous':
                        raise Blocked(f'Member {member_id}: joining time overlaps a window boundary')
                    if c.get('left') and row.locator(c['left']).count():
                        continue
                    found[member_id] = {'id': member_id, 'platform': self.platform,
                        'joined_lo': lo.isoformat(), 'joined_hi': hi.isoformat(),
                        'evidence': evidence}
                except Exception as exc:
                    issues.append(str(exc))
            # Never treat a disabled next button or empty list as proof by itself.
            # The configured marker must mean ALL members/history was loaded.
            if self.page.locator(c['end']).is_visible() or (c.get('newest_first_verified') and crossed_start):
                reached_end = True
                break
            try:
                self.steps(c['advance'])
            except Exception as exc:
                issues.append(f'Member navigation failed: {exc}')
                break
        else:
            issues.append('Member scan reached its page limit')
        return list(found.values()), reached_end and not issues, issues

    def duplicate(self, text, since):
        """Inspect complete outgoing history. Private welcomes match across event URLs."""
        c = self.cfg[self.mode]
        self.assert_destination()
        seen = set()
        for _ in range(c.get('max_history_pages', 100)):
            crossed_start = False
            rows = self.page.locator(c['messages'])
            signature = tuple(rows.all_text_contents())
            if signature in seen:
                raise Blocked('Message history stalled before duplicate coverage was established')
            seen.add(signature)
            for row in rows.all():
                body = row.locator(c['message_text']).inner_text()
                posted = None
                if self.mode == 'public' and c.get('message_date'):
                    posted = aware(field(row, c['message_date']))
                    crossed_start |= posted < since
                if self.mode == 'private':
                    match = normalized(WELCOME.split('\n\n')[0]) in normalized(body)
                else:
                    match = normalized(body) == normalized(text)
                    match |= normalized(body) in {
                        normalized('Spaces left for 5 or 6-sided game this Saturday. Come join!'),
                        normalized('Spaces left for a 6-sided tournament this Saturday ⚽️🏆. Come join!')}
                    if match:
                        if not c.get('message_date'):
                            raise Blocked('Matching public post lacks a verified timestamp')
                        match = posted >= since
                if match:
                    if not row.locator(c['sent']).is_visible():
                        raise Blocked('Matching message exists but delivery is uncertain')
                    return True
            if self.page.locator(c['history_end']).is_visible() or (
                    self.mode == 'public' and c.get('newest_first_verified') and crossed_start):
                # Return to the main composer after traversing old history.
                self.steps(c.get('history_return', []))
                self.assert_destination()
                return False
            self.steps(c['history_more'])
        raise Blocked('Duplicate scan exceeded history limit')

    def prepare(self, text, fill):
        self.assert_destination()
        c = self.cfg[self.mode]
        self.steps(c.get('compose_open', []))
        editor = unique(self.page.locator(c['editor']))
        current = editor.input_value() if editor.evaluate('e => /^(INPUT|TEXTAREA)$/.test(e.tagName)') else editor.inner_text()
        if current.strip():
            raise Blocked('Existing draft present; not overwriting it')
        if fill:
            editor.fill(text)
            value = editor.input_value() if editor.evaluate('e => /^(INPUT|TEXTAREA)$/.test(e.tagName)') else editor.inner_text()
            if normalized(value) != normalized(text):
                raise Blocked('Composer text did not match requested text')

    def send(self, text, before_click):
        from playwright.sync_api import expect
        self.assert_destination()
        c = self.cfg[self.mode]
        button = unique(self.page.locator(c['send']))
        if not button.is_enabled():
            raise Blocked('Send control disabled')
        before_click()
        button.click(timeout=5000)
        # Locator must target outgoing published content only, never the draft.
        row = self.page.locator(c['messages']).filter(has_text=text)
        expect(row).to_have_count(1, timeout=60000)
        expect(row.locator(c['sent'])).to_be_visible(timeout=60000)
        if normalized(row.locator(c['message_text']).inner_text()) != normalized(text):
            raise Blocked('Published message differs from requested text')
