# Geneva Football Friendly Thursday reminder

Python code and a GitHub Actions workflow for Thursday **11:00 Europe/Zurich**
(12:00 Sofia). Your laptop and Codex can be off. The sending job uses an
**always-on Linux cloud VM registered as a self-hosted GitHub Actions runner**.
GitHub-hosted runners run the offline tests; the cloud VM retains browser sessions
and receipt history. A VM is an additional hosting service, not provided by this code.

## Readiness

This is implemented automation logic with a configurable Playwright browser adapter,
**not a deployed or live-verified service**. No cloud VM has been provisioned and no
accounts have been linked to one. `config.json` intentionally contains blank UI
selectors: the general Meetup Discussion composer, private conversation routes,
membership rows and history controls must be verified on the actual signed-in UI.
The shipped configuration refuses to send. Do not mark it verified just to bypass
that check. If a platform does not expose the necessary joining evidence, that part
cannot be made fully unattended by filling in arbitrary selectors.

The previous hand-maintained `members.csv` is no longer read. Discovery traverses
configured live membership lists/join notices and pagination/history controls.
Name-only WhatsApp notices without a stable phone identifier are reported as
unresolved; the code never guesses a number from a name. Date-only membership
records overlapping a window boundary are reported and hold back the checkpoint.

## Behaviour

- Verify one active following-Saturday event in the correct Meetup group: Campus
  des Nations, arrival 10:45, play 11:00–12:00. Use its actual URL for private welcomes.
- Post the three-paragraph public message in WhatsApp main Announcements and
  Meetup general group Discussions, not event comments. No image or added link.
- Per platform, scan since its last complete membership check through the scheduled
  Thursday 11:00. With no checkpoint use 168 elapsed hours. This initial window may
  differ by an hour from seven local calendar days across a DST transition.
- Save eligible members to the pending queue before advancing a complete-coverage
  checkpoint. Incomplete or ambiguous coverage does not advance it. Pending sends
  are retried even after a checkpoint advances.
- Check recorded and live outgoing messages before sending. A previously verified
  welcome is never sent again, even for a new event. Accounts on the two platforms
  remain independent.
- Persist an uncertain receipt before clicking Send. A crash or ambiguous result
  does not trigger a blind resend. A live verified match can resolve uncertainty;
  otherwise inspection is required.
- Permit delayed sends only on the scheduled Thursday after 11:00 Geneva. A stale
  workflow from a previous day/week cannot send. Check the time again before each
  new send. GitHub schedules may be late or dropped; no exact-minute guarantee.
- Continue independently available actions and fail the job on blockers or
  incomplete coverage. Private detailed reports and screenshots stay on the VM;
  GitHub gets counts and status only.

The Thursday script only communicates the Friday deadline. It does not close RSVPs,
count Friday attendance, cancel sessions, or contact the venue. Sunday and Friday
automations are not modified.

## Public message

```text
Friendly football this Saturday ⚽️🏆 5-or 6-a-side depending on numbers.

Please register on Meetup by Friday at 10:45 so we can count you in for Saturday’s game. We review registrations on Friday at 10:50 to notify the venue before 11:00. All times are Geneva local time.

We need at least 10 registered players to confirm the session, so please register early - even if you’ve already told us you’re coming!
```

## Private message

```text
Hi! 👋 I noticed yоu have joined the Geneva Football Friendly Group. We organize a friendly international game every Saturday from 10:45–12:00 at Campus des Nations in Geneva.

Meetup link to register for the next event: {verified Saturday event URL}
```

## Files

```text
.github/workflows/football-reminder.yml
football_reminder/
  reminder.py            # schedule, checkpoints, receipts, queue, orchestration
  browser.py             # event verification and configurable live UI adapter
  config.json            # safe template; sending disabled
  requirements.txt
  test_reminder.py
  README.md
  GITHUB_ACTIONS.md       # cloud deployment
  SELECTORS.md            # one-time site integration and limitations
  .gitignore
```

## Local/offline validation

Python 3.12 recommended. From the repository root:

```bash
python -m pip install -r football_reminder/requirements.txt
python -m unittest discover -s football_reminder -p 'test_*.py' -v
```

Tests do not open browsers, access accounts or send messages. They verify core
logic, not the current live site's selectors. See [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md)
for installation, initial login, preview, migration and activation.

## Persistent private state

`FOOTBALL_STATE_DIR` must be an absolute directory outside the checkout. It contains
`config.json`, `browser-profile/`, `receipts.sqlite3`, `receipts-export.json`,
`last-report.json`, `run.log`, and last-page screenshots. Back it up privately;
browser profiles grant account access and receipts contain member identifiers.
The script never uploads these as GitHub artifacts or caches.

## Recovering an uncertain send

Check the live destination and the receipt before doing anything. A rerun reconciles
a matching sent message automatically. If the message cannot be found, the code
keeps the action uncertain rather than risking a duplicate. Do not delete the
database or all receipts to retry. A confirmed-not-sent action needs an operator
to review its single receipt and restore it to pending/absent; this is deliberately
not an automatic timeout-based retry.

## Known limitations

Sessions can expire or need phone approval; UI changes can break selectors. The
scanner requires usable joined-date and stable-identity fields, complete history
navigation or verified newest-to-oldest ordering. It cannot infer inaccessible
past joins, nor resolve departed/rejoined identities from names. The default adapter
does not automatically download and interpret Meetup member exports to resolve
date-only boundary records. Such records remain reported and the checkpoint stays
unchanged. These are live-integration limits, not solved by GitHub hosting.

Official references:
- [GitHub timezone-aware schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)
- [GitHub scheduled-event limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Self-hosted runners](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners)
- [Playwright authentication](https://playwright.dev/python/docs/auth)
