"""Conservative UI adapters. Selectors MUST be calibrated against the live accounts.

No guessed count, public fallback, group switch or blind mutation retry.
"""
import json
import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from core import Blocked


def one(locator, label):
    if locator.count() != 1:
        raise Blocked(f'{label}: expected one matching UI element')
    return locator


def json_events(value):
    if isinstance(value, list):
        for item in value:
            yield from json_events(item)
    elif isinstance(value, dict):
        types = value.get('@type')
        if types == 'Event' or (isinstance(types, list) and 'Event' in types):
            yield value
        if '@graph' in value:
            yield from json_events(value['@graph'])


class Meetup:
    def __init__(self, context, cfg):
        self.p = context.new_page()
        self.cfg = cfg

    def open(self, url):
        self.p.goto(url, wait_until='domcontentloaded')
        # Wait for hydration/authentication, not the initial misleading zero count.
        self.p.get_by_role('button', name=re.compile('Photo of the user Alex')).wait_for(timeout=45000)

    def find(self, date):
        self.open(self.cfg['group_url'] + 'events/')
        links = self.p.locator('a[href*="/geneve-football-friendly/events/"]').evaluate_all(
            '(nodes) => nodes.map(n => n.href)')
        urls = sorted({urljoin(self.cfg['group_url'], u.split('?')[0]) for u in links
                       if re.search(r'/events/[A-Za-z0-9]+/$', u.split('?')[0])})
        matches = {}
        for url in urls:
            self.open(url)
            for script in self.p.locator('script[type="application/ld+json"]').all_text_contents():
                for event in json_events(json.loads(script)):
                    start = datetime.fromisoformat(event['startDate'].replace('Z', '+00:00'))
                    if start.tzinfo is None:
                        raise Blocked('Meetup date lacks timezone')
                    if start.astimezone(ZoneInfo('Europe/Zurich')).date() == date:
                        matches[url] = event
        if len(matches) != 1:
            raise Blocked('Missing or ambiguous Saturday event (or structured metadata unavailable)')
        url, metadata = next(iter(matches.items()))
        self.open(url)
        start = datetime.fromisoformat(metadata['startDate'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Zurich'))
        end = datetime.fromisoformat(metadata['endDate'].replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Zurich'))
        if (start.hour, start.minute, end.hour, end.minute) != (10, 45, 12, 0) or end.date() != date:
            raise Blocked('Event time conflicts with arrival 10:45 / end 12:00')
        if self.cfg['venue'].casefold() not in json.dumps(metadata.get('location', {})).casefold():
            raise Blocked('Venue mismatch')
        body = self.p.locator('main').inner_text()
        if not re.search(r'11:00\s*[–-]\s*12:00', body):
            raise Blocked('Description does not confirm play 11:00–12:00')
        self.url = url
        return {'id': url.rstrip('/').split('/')[-1], 'url': url, 'date': date.isoformat(),
                'cancelled': 'EventCancelled' in metadata.get('eventStatus', '') or
                             bool(re.search(r'event (?:has been |is )cancel[l]?ed', body, re.I))}

    def count(self):
        self.open(self.url + 'attendees/')
        label = one(self.p.get_by_role('checkbox', name=re.compile(r'^\d+ Attendees$')), 'attendee count')
        name = label.get_attribute('aria-label') or label.inner_text()
        if not re.fullmatch(r'\d+ Attendees', name.strip()):
            # Accessible label sometimes comes from a wrapping label.
            name = label.evaluate('(e) => e.labels?.[0]?.innerText || e.parentElement.innerText')
        m = re.fullmatch(r'(\d+) Attendees', name.strip())
        if not m:
            raise Blocked('Cannot verify actual RSVP count')
        return int(m[1])

    def cancellation_controls(self):
        self.open(self.url)
        one(self.p.get_by_role('button', name='More', exact=True), 'More').click()
        one(self.p.get_by_role('menuitem', name='Cancel event', exact=True), 'Cancel event').click()
        radio = one(self.p.get_by_role('radio', name='Cancel this event', exact=True), 'single occurrence')
        radio.check()
        if not radio.is_checked():
            raise Blocked('Single-event cancellation not selected')

    def cancel(self):
        self.cancellation_controls()
        one(self.p.get_by_role('button', name='Confirm', exact=True), 'Confirm').click()
        self.p.get_by_role('heading', name='Your event has been canceled', exact=True).wait_for()
        return {'event': self.url, 'status': 'cancelled'}

    def comment(self, text, private):
        self.open(self.url)
        toggle = one(self.p.get_by_role('checkbox', name=re.compile('Send as a private comment')), 'comment privacy')
        toggle.set_checked(private)
        if toggle.is_checked() != private:
            raise Blocked('Comment privacy not verified')
        rows = self.p.locator(self.cfg['selectors']['meetup_comment_row']).filter(has_text=text)
        if rows.count():
            raise Blocked('Matching live comment already exists: reconcile before posting')
        one(self.p.get_by_role('textbox', name='Leave a comment...'), 'comment editor').fill(text)
        one(self.p.locator(self.cfg['selectors']['meetup_comment_submit']), 'comment send').click()
        row = self.p.locator(self.cfg['selectors']['meetup_comment_row']).filter(has_text=text)
        row.wait_for()
        one(row, 'posted comment')
        if private:
            one(row.locator(self.cfg['selectors']['meetup_private_badge']), 'private badge').wait_for()
        return {'event': self.url, 'private': private, 'text_verified': True}


class WhatsApp:
    def __init__(self, context, cfg):
        self.p = context.new_page()
        self.cfg = cfg
        self.s = cfg['selectors']

    def open(self):
        self.p.goto('https://web.whatsapp.com/', wait_until='domcontentloaded')
        self.p.locator(self.s['wa_search']).wait_for(state='visible', timeout=60000)
        search = one(self.p.locator(self.s['wa_search']), 'WhatsApp signed-in search')
        search.fill(self.cfg['whatsapp_group'])
        one(self.p.get_by_title(self.cfg['whatsapp_group'], exact=True), 'WhatsApp group').click()
        one(self.p.locator(self.s['wa_composer']), 'WhatsApp composer').wait_for()
        # Require an independently recorded chat JID, not only a potentially duplicate name.
        import os
        jid = os.environ['WHATSAPP_GROUP_JID']
        ids = self.p.locator(self.s['wa_message']).evaluate_all('(ns) => ns.map(n=>n.getAttribute("data-id"))')
        if not jid.endswith('@g.us') or not ids or any(
            not x or not x.startswith(('true_' + jid + '_', 'false_' + jid + '_')) for x in ids
        ):
            raise Blocked('WhatsApp main-group identity not verified')

    def targets(self, event):
        # Require the event URL on each anchor. No guessing by identical posters or date-only text.
        rows = self.p.locator(self.s['wa_message']).filter(has_text=event['url'].rstrip('/'))
        candidates = []
        for row in rows.all():
            if row.locator('[data-icon="msg-check"], [data-icon="msg-dblcheck"]').count():
                candidates.append((row.get_attribute('data-id'), row.locator(self.s['wa_media_image']).count() > 0))
        photos = [i for i, photo in candidates if photo]
        if len(photos) != 1 or not candidates:
            raise Blocked('Cannot uniquely identify event photo and latest recruitment post in loaded history')
        # DOM chronological order is a setup validation prerequisite. Photo can be recruitment too.
        return list(dict.fromkeys([candidates[-1][0], photos[0]]))

    def reply(self, anchor, text):
        self.check_existing(text)
        row = self.p.locator(self.s['wa_message'])
        matches = [r for r in row.all() if r.get_attribute('data-id') == anchor]
        if len(matches) != 1:
            raise Blocked('Reply anchor unavailable')
        before = set(self.p.locator(self.s['wa_message']).evaluate_all('(ns)=>ns.map(n=>n.dataset.id)'))
        matches[0].click(button='right')
        one(self.p.get_by_text('Reply', exact=True), 'quoted reply action').click()
        one(self.p.locator(self.s['wa_quote_preview']), 'quote preview').wait_for()
        # An exact event URL must appear in quote preview; selector must isolate the quote.
        event_urls = re.findall(r'https://www\.meetup\.com/geneve-football-friendly/events/[A-Za-z0-9]+',
                               matches[0].inner_text())
        if len(set(event_urls)) != 1 or event_urls[0] not in self.p.locator(self.s['wa_quote_preview']).inner_text():
            raise Blocked('Quote preview does not identify the event')
        editor = one(self.p.locator(self.s['wa_composer']), 'composer')
        editor.fill(text)
        editor.press('Enter')
        return self.verify_new(before, text)

    def verify_new(self, before, text=None, image=False):
        # Poll UI only. Never repeat the send on timeout.
        for _ in range(30):
            for row in self.p.locator(self.s['wa_message']).all():
                mid = row.get_attribute('data-id')
                if mid in before or not mid:
                    continue
                if text and text not in row.inner_text():
                    continue
                if image and not row.locator(self.s['wa_media_image']).count():
                    continue
                if row.locator(self.s['wa_sent']).count():
                    if not hasattr(self, 'verified_this_run'):
                        self.verified_this_run = set()
                    self.verified_this_run.add(mid)
                    return {'message_id': mid, 'sent': True}
            self.p.wait_for_timeout(1000)
        raise Blocked('WhatsApp send state uncertain; inspect before retry')

    def text(self, text):
        self.check_existing(text)
        before = set(self.p.locator(self.s['wa_message']).evaluate_all('(ns)=>ns.map(n=>n.dataset.id)'))
        editor = one(self.p.locator(self.s['wa_composer']), 'composer')
        editor.fill(text)
        editor.press('Enter')
        return self.verify_new(before, text)

    def check_existing(self, text):
        # Existing live posts may have been made outside this ledger. Require review,
        # except our first cancellation reply when the second anchor is intentional.
        import datetime as dt
        today = dt.datetime.now(ZoneInfo('Europe/Zurich')).date()
        dates = {today.strftime('%m/%d/%Y'), today.strftime('%d/%m/%Y'),
                 f'{today.month}/{today.day}/{today.year}', f'{today.day}/{today.month}/{today.year}'}
        for row in self.p.locator(self.s['wa_message']).filter(has_text=text).all():
            if not row.locator(self.s['wa_sent']).count():
                continue
            mid = row.get_attribute('data-id')
            if mid in getattr(self, 'verified_this_run', set()):
                continue
            stamps = row.locator('[data-pre-plain-text]').evaluate_all(
                '(ns)=>ns.map(n=>n.getAttribute("data-pre-plain-text"))')
            if not stamps or any(d in stamp for stamp in stamps for d in dates):
                raise Blocked('Matching live WhatsApp message found; reconcile it with receipts before sending')

    def photo(self, path):
        before = set(self.p.locator(self.s['wa_message']).evaluate_all('(ns)=>ns.map(n=>n.dataset.id)'))
        one(self.p.locator(self.s['wa_attach']), 'attach').click()
        one(self.p.locator(self.s['wa_photo_input']), 'photo input').set_input_files(str(path))
        one(self.p.locator(self.s['wa_media_send']), 'media send').click()
        return self.verify_new(before, image=True)
