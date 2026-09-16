# linkedin-autopilot

Automates your own LinkedIn account: writes and schedules daily posts, and finds
posts worth engaging with, drafts a reply, and waits for you to approve it.

Built around one assumption — that anything appearing under your real name
should be something you would have written yourself. Drafts are queued for
review by default, every limit is deliberately low, and a single file on disk
stops everything mid-run.

---

## Read this before you install

The two halves of this tool stand on very different ground.

**Posting is fully supported.** It goes through LinkedIn's official Posts API
using the `w_member_social` scope, which exists precisely so an application can
post on behalf of the member who authorized it. Nothing about it is a grey area.

**Likes and comments on other people's posts are not.** LinkedIn's public API
does not expose feed reading, likes, or comments on other members' posts to
individual developers. There is no sanctioned path, so that half of this tool
drives a real Chromium session logged in as you. That is automated access to the
LinkedIn site, which section 8.2 of the
[LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement)
prohibits. Accounts doing it can be restricted or permanently banned, and no
amount of careful pacing makes that risk zero.

So the engagement half ships **disabled**, and its default mode is `suggest`,
which reads the feed and fills a review queue but never clicks anything. You can
use this tool purely for scheduled posting and never turn the rest on. If you do
turn it on, you are making an informed choice about your own account. There is
no stealth or fingerprint-spoofing code here and none will be added — the
mitigation on offer is low volume and human review, not evasion.

`docs/ENGAGEMENT.md` covers this in more detail.

---

## What it does

| | How | Default |
|---|---|---|
| Daily post | Official LinkedIn Posts API | On, queued for approval |
| Relevance scoring | Keyword match, then Claude on survivors | On |
| Comment drafting | Claude, with a specificity check | Queued for approval |
| Likes | Browser session | Off |
| Comments | Browser session | Off |

Post and comment drafts are written by Claude Opus 5 through the Anthropic API.
The prompts are constraint-heavy on purpose: no engagement bait, no opening
with a rhetorical question, no generic praise. A draft that comes back as
flattery with nothing specific in it is rejected before you ever see it.

---

## Setup

Requires Python 3.11 or newer.

```bash
git clone https://github.com/thakkarbhai36-dev/link.git
cd link
python -m pip install -e .
autopilot init
```

`autopilot init` writes `config.yaml` and `.env` from the bundled examples.

### 1. Anthropic API key

Put it in `.env` as `ANTHROPIC_API_KEY`. If you have run `ant auth login`, the
SDK picks that profile up and the variable is optional.

### 2. LinkedIn application

1. Create an app at <https://www.linkedin.com/developers/apps>.
2. Add two products: **Share on LinkedIn** and **Sign In with LinkedIn using
   OpenID Connect**. Both need to be approved before the scopes work.
3. Under Auth, add this exact redirect URL:
   `http://localhost:8765/callback`
4. Copy the client ID and secret into `.env`.

Then authorize:

```bash
autopilot auth login
```

A browser opens, you approve, and the token lands in `state/tokens.json` with
owner-only permissions. Most developer apps do not get refresh tokens, so expect
to repeat this roughly every two months. `autopilot auth status` tells you when.

### 3. Make it sound like you

Open `config.yaml` and edit two things properly:

- `posting.voice` — a few sentences describing how you actually write. This is
  the single highest-leverage field in the file. "Senior backend engineer, ten
  years in, writes plainly, prefers a number over a claim" produces markedly
  different output from an empty string.
- `posting.topics` — what you want to be known for. Topics rotate and respect a
  cooldown so a week of posts does not circle one subject.

### 4. First draft

```bash
autopilot post draft
```

This generates a post and queues it. Nothing is published. Read it, then:

```bash
autopilot review show 1      # full text and the angle it took
autopilot review edit 1      # opens your editor
autopilot review approve 1   # publishes
```

While `dry_run: true` in `config.yaml`, approving still publishes nothing — it
walks the whole path and stops at the last step. Turn `dry_run` off once you
trust what the queue is producing.

---

## Running it continuously

```bash
autopilot run
```

Starts the scheduler: a post job at the times in `posting.times`, and, if
engagement is on, a feed scan a few times a day. Every job start is offset by a
random interval up to `safety.schedule_jitter_seconds`, so it never fires at
exactly the same minute twice.

Run it under systemd, launchd, tmux, or whatever you already use. It needs to
stay resident; it is not a cron one-shot.

### Stopping it

```bash
autopilot stop     # creates state/STOP — everything halts, mid-run
autopilot resume   # deletes it
```

The kill switch is checked before every single action, not just at job start.
`touch state/STOP` from any shell does the same thing.

---

## Turning on engagement

Only after the posting half has been running for a while and you are happy with
it.

```bash
pip install -e ".[browser]"
python -m playwright install chromium
autopilot browser login      # sign in by hand, including 2FA, then close the window
autopilot browser check
```

Then in `config.yaml` set `engagement.enabled: true`, fill in
`engagement.interests`, and leave `mode: suggest`.

```bash
autopilot engage scan
```

This reads your feed, scores each post against your interests, drafts comments
for the ones that clear the bar, and queues everything. It does not click. Look
at what it queued:

```bash
autopilot review list
autopilot review show 4
autopilot review approve 4
```

`mode: auto` skips the queue and acts directly. It exists, it works, and it is
the setting most likely to get you in trouble — both with LinkedIn and with the
people whose posts you are commenting on. Leave it on `suggest` unless you have
a reason.

---

## Commands

```
autopilot init                    write config.yaml and .env
autopilot status                  quotas, limits, queue depth
autopilot doctor                  check the setup is complete
autopilot run                     start the scheduler
autopilot stop / resume           kill switch

autopilot auth login|status|logout
autopilot post draft [--topic X]  generate and queue
autopilot post now [--topic X]    generate and publish
autopilot engage scan [--debug]   read the feed and queue engagement
autopilot review list|show|edit|approve|reject|run
autopilot browser login|check
```

---

## Limits

Set in `config.yaml` under `safety` and the per-type sections. Defaults:

| Limit | Default |
|---|---|
| Posts per day | 1 |
| Likes per day | 15 |
| Comments per day | 5 |
| All actions per day | 25 |
| Comments per author per week | 1 |
| Gap between actions | 45 to 180 seconds, random |
| Active hours | 08:00 to 21:00 local |

Every one is enforced locally before an action runs, and counted against the
local day in your configured timezone. The global ceiling wins over the
per-type limits, so raising one number does not quietly raise the total.

---

## When the feed scraper breaks

It will. LinkedIn ships front-end changes constantly and every selector in
`src/linkedin_autopilot/engagement/selectors.py` is a guess about markup you do
not control. When a scan starts finding nothing:

```bash
autopilot engage scan --debug
```

That saves a screenshot and the page HTML to `state/screenshots/`. Open the
browser the tool launched, find the element, and update the matching entry in
`selectors.py`. Each field holds a tuple of fallbacks tried in order, so you can
add a new selector without removing the old one.

Nothing else in the codebase touches LinkedIn markup. That file is the whole
blast radius.

---

## What is stored where

```
state/
├── autopilot.sqlite3     queue, counters, seen posts, topic history
├── tokens.json           LinkedIn access token, mode 0600
├── browser-profile/      Chromium profile with your session cookie
├── screenshots/          debug snapshots
└── logs/autopilot.log    rotating log
```

Everything is local. Nothing is sent anywhere except LinkedIn and the Anthropic
API. `state/` and `.env` are both gitignored; check that before you commit.

---

## Development

```bash
make dev        # install with dev and browser extras
make test
make lint
make typecheck
```

Tests cover the parts that make decisions — config validation, guardrails,
quotas, scoring, draft validation, topic rotation, payload construction. They do
not hit LinkedIn or the Anthropic API, and they do not need a browser.

---

## Licence

MIT. See `LICENSE`.

This is a tool for automating your own account. It is not for operating accounts
you do not own, running engagement pods, or generating volume. The rate limits
are low because that is the intended use, not an obstacle to route around.
