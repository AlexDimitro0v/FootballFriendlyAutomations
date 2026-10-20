# Sunday photo announcement

Python 3.10+ script for this project's **Sunday announcement only**. No Thursday reminders, private welcomes, Meetup posts, attendance decisions, booking emails or payments.

**GitHub Actions deployment:** see [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md). The supplied workflow runs the browser on a persistent Linux cloud VM registered as a self-hosted runner; your Windows computer can be off. It needs a configured cloud runner, not just a GitHub-hosted disposable job. Cloud paths/session/receipts are controlled by `SUNDAY_STATE_DIR` and `config.cloud.json`.

Every Sunday at **11:00 Europe/Zurich**, announce the following Saturday (six days later) in the main Genève Football Friendly WhatsApp announcement group. Geneva daylight saving is handled with `zoneinfo`, independent of your device's timezone. A delayed run may send until Sunday ends; other days are blocked. The scheduler polls every 15 seconds, so delivery is not second-exact.

One photo, unchanged: `../work/weekly-announcement/session-photo.png`.

Exact caption:

```text
Link for registrations: {verified event URL}
```

## Setup on Windows

From this directory, with Python installed:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ../../requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe announce.py login --inspect
```

This uses a separate persistent Chromium profile in `state/browser-profile`. Link WhatsApp with your phone. It does not copy or extract the Codex/Chrome session. Meetup public browsing is used; sign in manually if access requires it. Do not bypass a CAPTCHA or browser security warning.

For a cloud runner, state lives in the absolute `SUNDAY_STATE_DIR` outside the checkout. The default config then becomes `$SUNDAY_STATE_DIR/config.json`. `--config PATH` can override the config location, and photo/receipt paths resolve relative to that file. Use `config.cloud.json` on Linux; the Windows legacy path in `config.json` is rejected on Linux.

### Required first-time UI verification

The Python browser flow has **not been tested against your live accounts**. Default selectors are provisional; photo controls are deliberately empty. Site/language changes can break selectors, so the script fails closed. Use Playwright Inspector to select unique controls and fill `config.json`:

| Selector | Must identify |
|---|---|
| `group_header` | The open main announcement group's header |
| `main_composer` | Its empty main text composer |
| `reply_context` | Any quoted/reply context that should block a new announcement |
| `outgoing_messages` | Outgoing message containers in this group, excluding previews |
| `photo_in_message` | A photo control **inside one outgoing message** |
| `sent_in_message` | Sent/delivered/read indicator **inside that same message**, never the pending clock |
| `attach_button` | Main announcement attachment control |
| `photos_button` | Photos/videos menu option triggering the file chooser |
| `preview_photo` | The single selected photo in the upload preview, not a document |
| `caption_editor` | The photo's contenteditable caption field |
| `preview_destination` | Preview element whose text is exactly the group name |
| `send_photo_button` | The photo preview's Send button |

Verify the destination against `https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX`, not the Admins subgroup. Then set `verified_invite` to that exact URL and `ui_verified` to `true`. These are setup attestations, not substitutes for the per-run destination and sent-state checks. Use WhatsApp's English UI for the current header/search checks. If group search returns multiple exact-name matches, the script stops; refine the group selection against the observed UI before unattended use.

Check existing chat history before first enabling sends. Live duplicate detection covers **loaded outgoing messages only**, including Meetup short links, and is conservative: any matching outgoing event link suppresses a new announcement. It cannot establish full server history. Preserve/import receipts; verify any older unrecorded announcement manually. Do not delete receipts to resolve an uncertain send.

## Commands

```powershell
# Discover and verify next Saturday, print the proposed photo/caption. No WhatsApp upload/send.
.\.venv\Scripts\python.exe announce.py run

# Perform one real run (only Sunday 11:00–23:59 Geneva time).
.\.venv\Scripts\python.exe announce.py run --send

# Keep running and attempt once each Sunday. Leave the machine awake and online.
.\.venv\Scripts\python.exe announce.py schedule --send

# Offline tests; these never open a browser or send anything.
.\.venv\Scripts\python.exe -m unittest discover -s . -p test_announce.py -v
```

`--headless` is optional after visible-browser validation; login must be visible. A dry run on a non-Sunday previews the Saturday after the next Sunday. Nothing in this code installs a background task, changes the existing Codex schedule, or launches itself after reboot.

**Choose one sender:** the existing Codex Sunday automation remains active. Pause that automation before enabling this script's `--send`. The script imports both known Codex receipt journals and appends successful/duplicate outcomes to the current shared JSON journal, but there is no cross-scheduler transaction or lock. Running both can race and duplicate a post. Thursday and other automations can remain unchanged.

## Outcomes and limits

| Condition | Result |
|---|---|
| One matching active Saturday event | Verifies Campus des Nations, play 11:00–12:00 and arrival 10:45 from event metadata and visible page |
| Cancelled, wrong venue/time, missing/ambiguous metadata | Stops with a blocker, sends nothing |
| No matching event in the loaded upcoming list | Stops; inspect event listing/pagination. It does not guess a URL |
| Existing receipt or matching loaded outgoing event link | Skips duplicate |
| Signed out, wrong group, existing draft, reply context, missing selector | Stops, preserving the draft |
| Time window ends during discovery/upload | Rechecks before Send and stops |
| Click fails or verification is uncertain | Durable `uncertain` record prevents blind resend, even after restart |
| Verified outgoing photo + caption + sent indicator | Records success locally and in the shared journal, saves screenshot |
| Shared receipt saving fails after sending | Reports that the message was sent, preserves SQLite receipt, does not resend |
| Standalone `run` fails | Nonzero exit code with error on console and in `state/run.log` |
| Scheduled attempt fails | Logs blocker; no automatic same-day retries. Later Sundays still run |

Notifications are **console and log messages**, not push notifications or messages back into Codex. An external scheduler/monitor can capture the exit status for `run --send`. A continuously running `schedule` process must be monitored for blockers and restarts; it does not report days on which the process was switched off. No claim of a reliable cloud deployment is made.

The script uses Meetup's live upcoming links and per-event JSON-LD plus visible text, not a configured GraphQL API. It intentionally blocks if the site changes its schema or displays unverifiable dates/status. It never interprets missing data as an active event. Registration counts do not affect this Sunday announcement.

The event cannot be atomically checked together with a WhatsApp send: it could change just after verification. Likewise, exactly-once delivery cannot be guaranteed across independent services. Persisting intent before clicking and refusing uncertain retries prioritizes avoiding duplicates; human review may be required after a crash.

Keep `state/` private: it contains login sessions, receipts, logs and chat screenshots. Never commit it. `config.json` contains no credentials. Screenshots are local evidence only.

Implementation references: [Playwright file uploads](https://playwright.dev/python/docs/input#upload-files), [persistent browser contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context), and [Python IANA timezone support](https://docs.python.org/3/library/zoneinfo.html).
