# Run the Sunday announcement from GitHub Actions

Your Windows computer and Codex do not need to be running after cloud setup. GitHub stores the code and schedules the workflow; an **always-on Linux cloud VM registered as a self-hosted GitHub Actions runner** runs the browser. This is not an `ubuntu-latest` hosted-browser deployment: a cloud VM is required and may have a hosting cost. The workflow's offline tests do run on GitHub-hosted Ubuntu.

The persistent VM retains WhatsApp's linked browser profile and the send journal. Ordinary GitHub-hosted jobs start on fresh machines; this implementation does not attempt to copy authenticated profiles between machines or treat an Actions cache as reliable duplicate-prevention storage. It also avoids publishing session files as workflow artifacts. Initial linking and occasional re-linking still need your phone and access to the VM's desktop.

## What is configured

| Item | Value |
|---|---|
| Workflow | `.github/workflows/sunday-announcement.yml` |
| Schedule | Sunday 11:00, timezone `Europe/Zurich` |
| DST | GitHub's IANA timezone schedule plus Python's independent Geneva-time checks |
| Trigger | Scheduled run or **Run workflow** manual dispatch |
| Scheduled sends enabled | Only after `SUNDAY_AUTOMATION_ENABLED=true` is set |
| Runner labels | `self-hosted`, `linux`, `x64`, `football-sunday` |
| Photo | Same unchanged user-supplied poster |
| Caption | `Link for registrations: {verified event URL}` |
| Output | Sanitized GitHub job summary; detailed logs/screenshots remain on the VM |
| Send window | Sunday 11:00 until local midnight; no other-day catch-up |

GitHub schedules may be delayed or dropped. A run that starts late on Sunday remains eligible; a Monday run fails without posting. Old runs re-run the following Sunday also fail: the script checks the original workflow creation date. GitHub scheduling is not an exact 11:00 delivery guarantee. The workflow must be on the repository's default branch.

## 1. Upload the code to a private GitHub repository

Include these paths:

```text
.github/workflows/sunday-announcement.yml
sunday_announcement/
```

The poster is included at `sunday_announcement/assets/session-photo.png`. Do not upload `state/`, browser profiles, local chat screenshots, journals, or unrelated project files. The subdirectory `.gitignore` excludes runtime state. Keep repository write access restricted: code running on this runner has access to its authenticated browser. Do not add pull-request triggers to this runner.

This change does not create or push a GitHub repository. No Git remote was configured in the workspace during preparation.

## 2. Prepare a cloud Linux runner

Use a Linux x64 VM with persistent storage and a private desktop connection for browser linking. Install Python 3.10+ (3.12 recommended). Register the VM using **Repository Settings → Actions → Runners → New self-hosted runner** and add the custom label `football-sunday`. Install the runner as a service under the same non-root account used for browser setup.

Choose a private persistent directory **outside the runner checkout**. Example commands from a copy of the repository on the VM:

```bash
export SUNDAY_STATE_DIR="$HOME/football-sunday-state"
umask 077
mkdir -p "$SUNDAY_STATE_DIR"
chmod 700 "$SUNDAY_STATE_DIR"
python3 -m venv "$SUNDAY_STATE_DIR/venv"
"$SUNDAY_STATE_DIR/venv/bin/pip" install -r ../../requirements.txt
"$SUNDAY_STATE_DIR/venv/bin/python" -m playwright install --with-deps chromium

# Initial setup only: do not overwrite your verified configuration on upgrades.
cp -n sunday_announcement/config.cloud.json "$SUNDAY_STATE_DIR/config.json"
cp -n sunday_announcement/assets/session-photo.png "$SUNDAY_STATE_DIR/session-photo.png"
```

Browser system dependencies may require administrator access. Run login and the runner service under the same OS account so they share browser binaries and state. Repository upgrades should not replace `config.json`, session data or receipts.

## 3. Migrate receipts and link the browser

Privately transfer the current `work/weekly-announcement/sent-events.json` to `$SUNDAY_STATE_DIR/sent-events.json`. If you also have a separate legacy journal, transfer it to `$SUNDAY_STATE_DIR/imported-receipts.json`. This is a one-time transfer; later runs do not read your Windows disk.

If no historical receipts exist, inspect WhatsApp history first, record any prior announcements, and only then initialize `sent-events.json` with `[]`. Missing files are not proof that nothing was posted. Never replace an existing receipt file with an empty one.

In the VM's desktop terminal:

```bash
export SUNDAY_STATE_DIR="$HOME/football-sunday-state"
"$SUNDAY_STATE_DIR/venv/bin/python" sunday_announcement/announce.py login --inspect
```

Scan WhatsApp's QR code from your phone. Verify the main announcement group against `https://chat.whatsapp.com/GvSCWqkkIwWG2hIHEyLyNX`. Complete the selectors in the **persistent** `$SUNDAY_STATE_DIR/config.json`, following the selector table in [README.md](README.md). Set `ui_verified=true` and `verified_invite` only after verification. Press Enter in the login terminal to close the browser cleanly and save its session. This also creates/imports the persistent SQLite receipt database.

The site selectors are still provisional; the file-upload controls must be filled and tested on the VM. A read-only dry run validates Meetup discovery, the poster path and caption; it does **not** validate WhatsApp uploading or sending. Do not assume a green dry run proves end-to-end delivery.

## 4. Set GitHub repository variables

Under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
|---|---|
| `SUNDAY_STATE_DIR` | The absolute VM directory, e.g. `/home/runner/football-sunday-state` |
| `SUNDAY_AUTOMATION_ENABLED` | Leave unset/`false` while setting up; set `true` for scheduled sending |

No WhatsApp passwords, QR codes, cookies or browser profiles belong in GitHub variables/artifacts. Authentication stays on the VM. The workflow's default GitHub token has read-only repository and Actions permissions; it only reads its original run timestamp.

## 5. Validate, then switch schedulers

1. Run the workflow manually with `send=false`. Review the job summary and `$SUNDAY_STATE_DIR/last-run.log`.
2. Fix account, UI selector or live event-verification blockers. No live-site tests were performed while generating this code.
3. Pause the existing **Codex Sunday announcement** before enabling the cloud sender. Do not run the old continuous `schedule --send` process alongside Actions. The two schedulers have separate state and could race.
4. Set `SUNDAY_AUTOMATION_ENABLED=true`. Real sends, including manual ones, are allowed only during Sunday's Geneva posting window.
5. Observe the first Sunday run and verify the outgoing photo/caption and the saved receipt. Later weeks can run with your Windows computer off.

The current Codex automation was **not paused** by this code change. No cloud VM or GitHub workflow has been deployed from this workspace.

## Maintenance and failures

- Keep the cloud VM and runner service running. If the VM is off, GitHub cannot execute the browser job.
- Set up GitHub workflow notifications for failures. The workflow creates a sanitized result in its job summary, not a WhatsApp/Meetup notification to you.
- A pending/uncertain send stays in SQLite across runner restarts. Check the actual chat before resolving it; no automatic reset or blind resend is implemented.
- Missing cloud state fails before sending; checkouts do not recreate empty state silently.
- Concurrency prevents overlapping workflow runs. An OS lock also protects script invocations using the same state directory.
- The workflow has a 20-minute timeout. Termination after an attempted click leaves durable intent for later review; the session and SQLite files stay on persistent storage.
- Back up state privately and encrypted. Back up SQLite while the process is stopped or with SQLite's backup API; never upload the linked browser session to a public repository or Actions cache.
- Re-link WhatsApp on the VM when the session expires. Changes to WhatsApp or Meetup may require selector/parser maintenance.
- Install updated dependencies in the state venv deliberately; the workflow does not install a new browser into the persistent authenticated environment on every run.

References: [GitHub schedule events and timezone support](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [self-hosted runners](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners), and [Playwright authentication state](https://playwright.dev/python/docs/auth).
