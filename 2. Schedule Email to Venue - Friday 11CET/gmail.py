"""OAuth Gmail reply, preserving thread, subject and RFC message references."""
import base64
import os
from email.message import EmailMessage
from email.utils import parseaddr
import requests
from core import Blocked
from messages import EMAIL


class Gmail:
    def __init__(self, cfg):
        self.cfg = cfg
        r = requests.post('https://oauth2.googleapis.com/token', timeout=30, data={
            'client_id': os.environ['GMAIL_CLIENT_ID'], 'client_secret': os.environ['GMAIL_CLIENT_SECRET'],
            'refresh_token': os.environ['GMAIL_REFRESH_TOKEN'], 'grant_type': 'refresh_token'})
        if r.status_code != 200:
            raise Blocked('Gmail OAuth refresh failed')
        self.headers = {'Authorization': 'Bearer ' + r.json()['access_token']}
        if self.get('profile')['emailAddress'].lower() != cfg['gmail_account'].lower():
            raise Blocked('Wrong Gmail account')
        # Browser /u/1 and Qgrc... identifiers are NOT Gmail API thread IDs.
        self.thread_id = os.environ['GMAIL_THREAD_ID']
        thread = self.get('threads/' + self.thread_id)
        self.messages = thread['messages']
        inbound = [m for m in self.messages if parseaddr(self.headers_of(m).get('from', ''))[1].lower()
                   == cfg['venue_email'].lower()]
        if not inbound:
            raise Blocked('Configured thread has no message from the verified venue email')
        self.parent = self.headers_of(inbound[-1])
        if self.parent.get('subject', '').removeprefix('Re: ').strip() != cfg['gmail_subject']:
            raise Blocked('Venue thread subject mismatch')
        if not self.parent.get('message-id'):
            raise Blocked('Venue parent message lacks Message-ID')

    @staticmethod
    def headers_of(m):
        return {h['name'].lower(): h['value'] for h in m['payload']['headers']}

    def get(self, path, params=None):
        r = requests.get('https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                         headers=self.headers, params=params, timeout=30)
        if r.status_code != 200:
            raise Blocked('Gmail read failed')
        return r.json()

    def preflight(self, day):
        # A pre-existing same-day outgoing/scheduled message needs human review;
        # do not duplicate the user's own cancellation email.
        for m in self.messages:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            sent = datetime.fromtimestamp(int(m['internalDate']) / 1000, ZoneInfo('Europe/Zurich')).date()
            if sent == day and set(m.get('labelIds', [])) & {'SENT', 'SCHEDULED', 'DRAFT'}:
                raise Blocked('Existing same-day outgoing/draft venue correspondence: review before sending')

    def send(self, event_id):
        msg = EmailMessage()
        msg['From'] = self.cfg['gmail_account']
        msg['To'] = self.cfg['venue_email']
        msg['Subject'] = self.parent['subject']
        msg['In-Reply-To'] = self.parent['message-id']
        msg['References'] = (self.parent.get('references', '') + ' ' + self.parent['message-id']).strip()
        msg['Message-ID'] = f'<football-cancel-{event_id}@football-friendly.invalid>'
        msg.set_content(EMAIL)
        r = requests.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                          headers=self.headers, timeout=30,
                          json={'threadId': self.thread_id,
                                'raw': base64.urlsafe_b64encode(msg.as_bytes()).decode()})
        if r.status_code != 200:
            raise Blocked('Gmail send failed or uncertain; do not retry automatically')
        sent = self.get('messages/' + r.json()['id'])
        if 'SENT' not in sent.get('labelIds', []) or sent['threadId'] != self.thread_id:
            raise Blocked('Gmail sent/thread verification failed')
        return {'message_id': sent['id'], 'thread_id': sent['threadId']}
