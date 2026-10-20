"""Local, interactive OAuth setup. pip install google-auth-oauthlib first."""
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import AuthorizedSession

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send']

if __name__ == '__main__':
    Path('.auth').mkdir(exist_ok=True)
    credentials = InstalledAppFlow.from_client_secrets_file('.auth/client_secret.json', SCOPES).run_local_server(port=0)
    Path('.auth/gmail-token.json').write_text(credentials.to_json(), encoding='utf-8')
    session = AuthorizedSession(credentials)
    cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
    response = session.get('https://gmail.googleapis.com/gmail/v1/users/me/threads',
                           params={'q': f'from:{cfg["venue_email"]} subject:"{cfg["gmail_subject"]}"',
                                   'maxResults': 100}, timeout=30)
    response.raise_for_status()
    threads = response.json().get('threads', [])
    Path('.auth/thread-candidates.json').write_text(json.dumps(threads, indent=2), encoding='utf-8')
    print('Credentials and thread candidates saved under .auth/. Never commit them. Verify the correct thread ID before configuring GitHub secrets.')
