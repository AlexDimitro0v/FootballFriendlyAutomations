# Geneva Football Friendly — Friday automation

Python + **GitHub-hosted Ubuntu runners**. The laptop can be switched off after setup.
This repository is a **deployment candidate, not a verified production integration**.
Business rules and checkpoint tests pass; live browser selectors, session portability,
private-comment support after cancellation, and WhatsApp sends have NOT been tested.
Do not enable live mode until those checks pass on your accounts.

## Behavior

Every Friday 10:50 **Europe/Zurich**, select the following Saturday at Campus des Nations,
arrival 10:45, finish 12:00, and verify the description says play 11:00–12:00.
Find the event dynamically; do not hardcode an old event ID.

| Registered Going count | Actions |
|---|---|
| 0–6 | Cancel this occurrence only; reply to Léo in the verified Gmail thread; post attendee-private cancellation comment on Meetup; quote-reply on WhatsApp to the latest event recruitment post and event photo. |
| 7–9 | Record a no-action decision. No email, comments, cancellations or confirmations. |
| 10+ | WhatsApp venue text, entrance photo, pitch photo; identical venue text in a Meetup comment. |

See `messages.py` for the exact user-approved text. Exactly 10 qualifies. Confirmed hosts
and registered guests count as Meetup counts them; waitlist and cancelled RSVPs do not.
No automatic refunds, credit ledger changes, or player transfers. Same-message WhatsApp
anchors are deduplicated. Private Meetup comments are never replaced by public comments.
WhatsApp replies remain group-visible.

## Important service constraints

* Meetup's official API requires active Pro membership plus approved OAuth access.
  A private cancellation-comment mutation and existing WhatsApp group quoted replies
  have not been verified through official APIs. This implementation uses **Playwright UI
  automation**, not invented API endpoints, for those services.
* This is an unofficial WhatsApp Web integration. Headless/cloud sessions may fail,
  require relinking, or be restricted by the service. Verify service terms/account eligibility
  before enabling unattended operation. This is not a claim of Meta API support.
* `bootstrap_sessions.py` exports cookies/local storage/IndexedDB encrypted with Fernet.
  Portability is not guaranteed, especially WhatsApp device keys. This version does not
  refresh that encrypted bundle remotely; rebootstrap if sessions expire. If restoring
  WhatsApp on a clean hosted runner fails, use a persistent cloud browser/VM solution
  instead of claiming that Actions alone is production-ready.
* Browser UI selectors change. `config.json` isolates the version-sensitive selectors.
  Several are conservative starting selectors, **not live-verified selectors**. The
  `UI_VALIDATED` gate deliberately blocks production until they are calibrated.
* GitHub schedules can be delayed or dropped. No promise of completion before 11:00.
  By default a run starting at/after 11:00 Geneva fails without side effects. Manual
  `allow_late` permits a Friday rerun only after the organizer considers the venue deadline.
  A run started before 11:00 can still finish after it. No Saturday catch-up with a false
  “tomorrow” email. A stricter deadline needs an earlier schedule or a more reliable scheduler.

## 1. Upload to GitHub

Use a **private repository**. Upload this folder's CONTENTS to the repository root,
including `.github/`, `assets/`, and the Python files. Do not publish personal browser
state, OAuth credentials, or unencrypted screenshots. Source contains your configured
account/venue addresses; adjust before making any source public.

Use the default branch (usually `main`) for the workflow. This package does not create a
GitHub repository or deploy anything automatically. The existing Codex automation is
unchanged: **disable it before enabling live GitHub scheduling** to avoid two independent
systems acting on the same event. Historical Codex receipts are not automatically imported.

## 2. Install locally and capture sessions

Python 3.12 recommended. From the repository root:
```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
pip install -r requirements.txt
python -m playwright install chromium
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Save that key securely as `SESSION_KEY` in your local shell and later as a GitHub Actions
secret. Run `python bootstrap_sessions.py`, sign in manually, and link WhatsApp using
your phone. Commit only the resulting **encrypted `sessions.enc`**, never the key or
plaintext session state. Use a dedicated automation browser session, not your main profile.
The captured file must fit GitHub repository limits; if too large, use encrypted object
storage with a separately reviewed downloader rather than silently truncating it.

In WhatsApp, verify the invite corresponds to the main announcement group, not Admins.
Using browser developer tools, inspect a message's `data-id` and record its group JID
(the `...@g.us` segment) as `WHATSAPP_GROUP_JID`. This is not the invite token.
The code checks the JID against loaded messages as well as selecting the exact group name.

## 3. Gmail OAuth

Enable Gmail API in your Google Cloud project; create a desktop OAuth client. Download
its JSON to `.auth/client_secret.json`. Install `google-auth-oauthlib` locally and run
`python setup_gmail.py`. Sign in as the configured Gmail account. It requests Gmail
read-only and send scopes, and saves credentials locally under ignored `.auth/`.

Set client ID, client secret, and refresh token from those credentials as GitHub secrets.
Review `.auth/thread-candidates.json`, verify **Football Pitch Bookings** with Léo, and
set its API thread ID. The Gmail browser `Qgrc...` URL is NOT an API thread ID.
Configure the OAuth application appropriately for durable unattended use; testing-mode
tokens may expire. No Gmail password is stored or used.

The sender account, venue address, parent Message-ID, and subject are checked. Replies
retain thread ID, subject, In-Reply-To and References. Only Léo is addressed; no reply-all.
Any same-day outgoing/draft/scheduled correspondence in the venue thread blocks a new
email pending review, rather than guessing whether it is a duplicate.

## 4. Durable state

Create a second **private state repository** with a `receipts.json` file containing `{}`
on its default branch. Create a fine-grained token restricted to that repository with
Contents read/write. Store the token as `STATE_TOKEN`; set `STATE_REPOSITORY=owner/repo`.

State is saved through GitHub's Contents API with SHA compare-and-swap before and after
each external action. It is NOT a disposable runner file or expiring cache. Failure to
checkpoint prevents further sends. Do not run another copy with a different state store.
The workflow concurrency group prevents overlapping runs within this source repository.

If a process dies after sending but before recording success, the action stays `pending`.
It will **not automatically resend**. Inspect the target service: mark it `done` with
evidence if successful, or remove that individual pending step only after proving it never
happened. Never clear a pending action merely to make a red workflow green. Other
independent steps may complete, and the report identifies partial completion.

## 5. GitHub environment configuration

Create environment **football-production**. No required reviewer if fully unattended
runs are intended; use reviewers during rollout if desired.

| Kind | Name | Value |
|---|---|---|
| Secret | `SESSION_KEY` | Fernet encryption key |
| Secret | `STATE_TOKEN` | Token scoped to private state repository |
| Secret | `WHATSAPP_GROUP_JID` | Verified main group `...@g.us` |
| Secret | `GMAIL_CLIENT_ID` | Google OAuth client ID |
| Secret | `GMAIL_CLIENT_SECRET` | Google OAuth client secret |
| Secret | `GMAIL_REFRESH_TOKEN` | Offline Gmail OAuth refresh token |
| Secret | `GMAIL_THREAD_ID` | Verified API thread ID |
| Variable | `STATE_REPOSITORY` | `owner/private-state-repo` |
| Variable | `LIVE_ENABLED` | `false` initially |
| Variable | `UI_VALIDATED` | `false` until live-account calibration passes |

Environment/repository secrets are not committed. Enable GitHub workflow failure
notifications for your account. Each run writes a GitHub job summary and exits nonzero
on blockers. No public issues or third-party notification emails are created automatically.

## 6. Validate, then enable

1. Run `python -m unittest discover -s tests -v`.
2. On a Friday after 10:50 Geneva, dispatch **Friday session decision** with `live=false`.
   Use `allow_late=true` only if intentionally testing after 11:00. Dry-run performs reads
   only and does not update the receipts or send anything. It verifies discovery/count,
   required sessions, Gmail account/thread for cancellations, and WhatsApp reply anchors.
3. Calibrate every selector in `config.json` using Playwright Inspector on your accounts.
   In a separate private test event/chat, verify cancel scope, attendee-private post and
   badge, quoted reply preview, message ticks, both photo uploads, and exact group JID.
   Dry-run alone does NOT test those write selectors. Never use the live football event
   as a disposable test. UI must be English; organizer display name is currently Alex.
4. Validate WhatsApp message container selector returns one row per message in chronological
   order, media selector excludes avatars, and quoted preview identifies the selected
   original. The current discovery scans loaded history only: it blocks if the photo isn't
   present. Each announcement/recruitment anchor must include the exact event URL. A new
   recruitment post without the URL is not reliably discoverable by this implementation.
   If your real posts do not follow this convention, extend and validate the adapter first.
5. Verify private comments remain available to registered attendees AFTER cancellation.
   If not supported, that notification is blocked. Do not weaken privacy to pass the test.
6. Disable the old Codex automation; set `UI_VALIDATED=true` and `LIVE_ENABLED=true` only
   after successful integration validation. Scheduled runs then perform real actions.
   For manual real runs select `live=true`; dates still must be the following Saturday.

No live actions were performed while generating this package. Both supplied venue photos
are included. No secrets, accounts, repository, or cloud deployment have been configured.

## Files

* `run.py`: orchestrator, dry-run, runtime date guards, partial results
* `core.py`: thresholds, timezone checks, durable action state machine
* `browser.py`: Meetup and WhatsApp headless UI adapters
* `gmail.py`: Gmail API verified thread reply
* `storage.py`: durable GitHub compare-and-swap ledger
* `bootstrap_sessions.py`, `setup_gmail.py`: one-time local authorization tools
* `.github/workflows/`: hosted schedule and isolated unit tests
* `tests/`: thresholds, DST, wrong-day/early/late guards, deduplication and crash behavior

## Official references consulted

* https://help.meetup.com/hc/en-us/articles/41453576628749-How-can-I-get-access-to-Meetup-s-API
* https://www.meetup.com/graphql/guide/
* https://developers.google.com/workspace/gmail/api/guides/threads
* https://playwright.dev/python/docs/auth
* https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
* https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

GitHub's current documented syntax supports `timezone: Europe/Zurich` next to the cron.
This handles CET/CEST without a laptop timezone dependency. Public-repository schedules
may be disabled after inactivity; private hosting is recommended for this project's data.
