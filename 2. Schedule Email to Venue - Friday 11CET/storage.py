"""CAS writes to a dedicated private GitHub state repository, not runner cache."""
import base64
import json
import os
import requests
from core import Blocked


class Store:
    def __init__(self, writable):
        repo = os.environ['STATE_REPOSITORY']
        self.url = f'https://api.github.com/repos/{repo}/contents/receipts.json'
        self.headers = {'Authorization': 'Bearer ' + os.environ['STATE_TOKEN'],
                        'Accept': 'application/vnd.github+json'}
        self.writable = writable
        r = requests.get(self.url, headers=self.headers, timeout=30)
        if r.status_code != 200:
            raise Blocked('State repository/receipts.json unavailable; initialize it with {} first')
        body = r.json()
        self.sha = body['sha']
        self.data = json.loads(base64.b64decode(body['content']))

    def save(self):
        if not self.writable:
            raise Blocked('Attempted to write state in dry-run')
        content = base64.b64encode(json.dumps(self.data, indent=2).encode()).decode()
        r = requests.put(self.url, headers=self.headers, timeout=30, json={
            'message': 'Record football automation progress', 'sha': self.sha, 'content': content})
        if r.status_code not in (200, 201):
            raise Blocked('State checkpoint failed; stop without further external actions')
        self.sha = r.json()['content']['sha']
