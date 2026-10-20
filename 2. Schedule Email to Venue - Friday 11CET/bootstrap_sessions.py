"""Run ONCE on your computer, then again only if sessions expire. Never on CI."""
import os
import json
from pathlib import Path
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

if __name__ == '__main__':
    key = os.environ.get('SESSION_KEY')
    if not key:
        raise SystemExit('Set SESSION_KEY to a Fernet key first; save it as a GitHub Actions secret')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(locale='en-US', timezone_id='Europe/Zurich')
        context.new_page().goto('https://www.meetup.com/geneve-football-friendly/')
        context.new_page().goto('https://web.whatsapp.com/')
        input('Sign into Meetup as organizer and link WhatsApp. Open the main group. Press Enter when ready: ')
        state = context.storage_state(indexed_db=True)
        Path('sessions.enc').write_bytes(Fernet(key.encode()).encrypt(json.dumps(state).encode()))
        browser.close()
    print('Created encrypted sessions.enc. Keep SESSION_KEY out of Git. Validate restored sessions in dry-run.')
