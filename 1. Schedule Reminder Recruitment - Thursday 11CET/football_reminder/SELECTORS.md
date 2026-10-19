# One-time browser integration

The template is deliberately incomplete. Site DOM controls are not a stable API.
Use Playwright Inspector on the VM (`login --inspect`) to locate and test each
control. Do not enable sending until both the selectors and navigation paths have
been verified on your own accounts. Existing sample WhatsApp selectors are candidates,
not a guarantee that the current UI uses them.

## Navigation actions

Each `open`, `compose_open`, `history_more`, `history_return` or `advance` field
contains a list of steps. Supported steps:

```json
[
  {"action": "goto", "value": "https://www.meetup.com/geneve-football-friendly/discussions/"},
  {"action": "wait", "selector": "VERIFIED_READY_SELECTOR"},
  {"action": "click", "selector": "VERIFIED_CONTROL_SELECTOR"}
]
```

Other operations are `fill` (selector + value), `press` (selector + key), and
`scroll` (selector of scrollable panel + signed pixel value, e.g. `-700`).
Private navigation values may use `{id}` for the verified member ID/phone number.
Configure navigation only: do not put Send, Post, approval, deletion or RSVP actions
in these lists. Final message submission belongs exclusively in the `send` selector.
All actions need unique selectors, never first-match guesses.

## Each platform's public/private channel

| Field | Meaning |
|---|---|
| `open` | Navigate to correct group or private conversation; profile-page navigation alone is not enough for Meetup DMs |
| `header` | One visible group/conversation header |
| `header_pattern` | Regex verifying the public group identity; required field for both modes |
| `identity` | For private mode, field relative to header exposing full phone number or Meetup `/members/ID/` href |
| `editor` / `send` | Main new-message composer and final submit button |
| `compose_open` | Optional steps to reveal the composer; no sending |
| `reply_context` | Active quote/reply chip in the composer, not historical quoted messages |
| `messages` | Outgoing messages/posts by your account only |
| `message_text` | Relative selector for the message body, excluding time, reactions and author |
| `sent` | Relative selector proving sent/delivered status or published post permalink; never pending spinner |
| `message_date` | Public-post timestamp field, ISO with UTC offset, relative to message row |
| `history_more` | Load/scroll toward older outgoing history |
| `history_end` | A verified end-of-history or empty-conversation marker; a transient empty list is insufficient |
| `history_return` | Return to main composer after scanning history |
| `newest_first_verified` | Optional true only if navigation starts at newest and traverses every row toward older history; permits stopping public scans after crossing the week boundary |

Meetup public URL is restricted to `/geneve-football-friendly/discussions/`.
WhatsApp group header must identify the announcement group. If your UI has a
community plus subgroups, configure `open` to choose Announcements explicitly.

## Member scanning

`members.open` must reach this group's actual member list or announcement join
history, with `ready` confirming that view loaded. Configure:

- `rows`: membership records or join notices, excluding ordinary messages.
- `id`: field containing the stable member ID (Meetup profile href) or full phone
  number (WhatsApp). A display name alone is not sufficient. Names that don't expose
  phone numbers are reported; automatic opening of name-only notices to resolve
  phone numbers is not implemented in this generic adapter.
- `joined`: field containing the actual **group** joining date, not account age.
- `left`: optional relative marker for a departed member. A separate historical
  leave notice is not automatically associated with a join row; verify current
  membership in the collection source if exclusion of departed members is required.
- `advance`: pagination or scrolling toward older entries.
- `end`: proof the complete list/history has ended, not a loading spinner or disabled
  button while a page is still loading.
- `newest_first_verified`: optional true after verifying navigation starts at the
  newest records and proceeds through every older record. A row strictly before
  the window start then proves coverage; otherwise a full end marker is required.

Field example for a Meetup row:

```json
{
  "id": {"selector": "a[href*='/members/']", "attribute": "href"},
  "joined": {
    "selector": "VERIFIED_GROUP_JOIN_DATE_ELEMENT",
    "pattern": "Joined\\s+(.+)",
    "format": "%b %d, %Y",
    "date_only": true
  }
}
```

For a timezone-aware ISO attribute use `format: "iso"`, an `attribute` such as
`datetime`, and a pattern extracting the entire timestamp. Date-only values are
treated as the whole local day. Only days wholly inside the window qualify;
boundary days are left unresolved. The generic adapter does not automatically
resolve boundary dates from an export. WhatsApp sometimes places dates in separate
day headings; unless a reliable date is available on each join row, custom field
extraction must be added and tested before that UI can be used unattended.

The scanner detects stalled/repeated pages and page limits; it reports incomplete
coverage rather than declaring there were no members. It persists independently
verified recipients even if later pagination fails.

## Final calibration

Set `verified_whatsapp_invite` to the supplied invite URL after comparing the actual
group. Set each platform's `ui_verified` only after checking the above behaviours.
Run `check-config`, then a preview. Before production, verify at least one authorized
send in the actual UI and its receipt/sent indicator. Offline tests cannot substitute
for this. No live messages are sent during the included test suite.
