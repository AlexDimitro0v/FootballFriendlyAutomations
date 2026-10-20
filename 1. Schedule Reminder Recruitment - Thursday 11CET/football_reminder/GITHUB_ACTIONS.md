# Run while your laptop and Codex are off

## Architecture

GitHub Actions schedule → always-on Linux cloud runner → persistent Chromium profile
→ WhatsApp Web and Meetup → private SQLite receipts/checkpoints.

The workflow does **not** execute account messaging on disposable `ubuntu-latest`
machines. Those machines run tests only. Moving a live WhatsApp session into a fresh
runner every week is not a verified authentication solution. A cloud self-hosted
runner is still GitHub Actions execution, but requires a separately hosted VM.

## 1. Prepare a private repository and cloud VM

Upload only `football_reminder/` source/config/docs and
`.github/workflows/football-reminder.yml`. Do not upload the entire personal workspace,
receipt journal, browser profile, real member CSV, screenshots or logs. No GitHub
password belongs in the code or chat.

Use a Linux x64 cloud VM with Python 3.12, enough memory for Chromium, and a protected
desktop session for initial login. Keep the VM running. In repository Settings →
Actions → Runners, follow GitHub's Linux runner installation instructions, give it
the label `football-thursday`, and install the service. Use the same non-root account
for the runner service and browser login. Use this authenticated runner only for
trusted default-branch code; do not add untrusted pull-request jobs to it.

## 2. Install under the runner's account

From the checkout directory on the VM:

```bash
export FOOTBALL_STATE_DIR=/home/runner/football-thursday-state
umask 077
mkdir -p "$FOOTBALL_STATE_DIR"
python3 -m venv "$FOOTBALL_STATE_DIR/venv"
"$FOOTBALL_STATE_DIR/venv/bin/python" -m pip install -r ../../requirements.txt
"$FOOTBALL_STATE_DIR/venv/bin/python" -m playwright install --with-deps chromium
cp football_reminder/config.json "$FOOTBALL_STATE_DIR/config.json"
```

Replace `/home/runner` with the actual runner account's home. Browser system packages
may require administrator access during setup. Do not run the browser as root.

## 3. Link accounts on the VM once

Run from the VM's desktop terminal (not a headless SSH-only session):

```bash
"$FOOTBALL_STATE_DIR/venv/bin/python" football_reminder/reminder.py login --inspect
```

Link WhatsApp from your phone and sign in to Meetup. Complete authentication and
any terms/CAPTCHA yourself. Verify the WhatsApp main announcement group against
`https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX`. The laptop's existing Chrome
session is not copied. Press Enter when login is complete so the profile closes cleanly.

## 4. Calibrate UI selectors

Follow [SELECTORS.md](SELECTORS.md). Configure the VM's `config.json`, not credentials
in GitHub. Confirm navigation reaches the main WhatsApp composer, general Meetup
Discussions, the correct private recipients and actual group joining dates.

```bash
"$FOOTBALL_STATE_DIR/venv/bin/python" football_reminder/reminder.py check-config
"$FOOTBALL_STATE_DIR/venv/bin/python" football_reminder/reminder.py run --headless
```

The second command is a preview: it scans live pages, validates recipients and
duplicate history but does not fill message text, send or advance membership
checkpoints. Inspect `last-report.json` and `run.log`. A check-config success means
fields are present, not that live selectors are correct. A preview still requires
the calibrated browser integration; blank placeholders produce a blocker.

## 5. Migrate old receipts before enabling sends

Privately copy `thursday-reminder-receipts.json` from the laptop to the VM, outside
the repository, then run:

```bash
"$FOOTBALL_STATE_DIR/venv/bin/python" football_reminder/reminder.py import-receipts \
  --file /private/path/thursday-reminder-receipts.json
```

This imports verified public posts and private welcomes (including the eight
WhatsApp welcomes already sent on October 15). It does not invent successful
membership checkpoints from old incomplete checks. If the Codex automation runs
again before cutover, copy/import its latest journal again.

## 6. Enable the cloud schedule

Only after live verification, disable the **Thursday** Codex automation to prevent
two independent senders. Do not disable Sunday or Friday. This code generation
does not disable any existing automation or deploy a VM.

In the VM config set:

```json
{
  "sending_enabled": true,
  "codex_thursday_disabled_confirmed": true
}
```

These are two fields in the existing full config; do not replace the whole config
with this excerpt. Set repository Actions variables:

| Variable | Value |
|---|---|
| `FOOTBALL_STATE_DIR` | The absolute persistent state directory on the VM |
| `THURSDAY_AUTOMATION_ENABLED` | `true` only when ready |

The workflow runs Thursday 11:00 with `timezone: Europe/Zurich`, handling Swiss
daylight saving directly. Run workflow manually with `send=false` first. A manual
`send=true` run obeys the Thursday timing guard and activation flags too.

## Timing and operation

GitHub schedules can be delayed or dropped, particularly at busy times. The script
accepts a delayed job later on that same Thursday; it refuses next-day catch-up and
reruns of a workflow originally created on another day. GitHub only runs scheduled
workflows from the default branch. There is no continuous laptop process.

Check Actions summaries and enable GitHub workflow-failure notifications. Detailed
errors remain on the VM. A red run may still have sent some messages: consult
receipts and pending queue before retrying. Subsequent runs preserve confirmed sends
and resume pending members; incomplete coverage retains the previous checkpoint.

When a login expires, reconnect using `login` on the VM desktop. Back up the private
state directory; losing it loses duplicate protection. The VM must remain online
for Actions to execute the browser job.
