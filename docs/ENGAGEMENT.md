# Automated likes and comments: what you are actually agreeing to

This document covers the engagement half of the tool. It ships disabled. Read
this before you change that.

## The short version

LinkedIn does not offer a supported way for an individual developer to read
their feed, like a post, or comment on someone else's post through an API. The
`w_member_social` scope covers posting to your own feed and nothing more. The
endpoints that would cover the rest sit behind the Marketing Developer Platform
and the Community Management API, both of which are partner programmes for
companies building products, not for individuals automating their own account.

So there is no sanctioned path, and this tool takes the unsanctioned one: it
drives a real Chromium session logged in as you, finds the button, and clicks
it. Section 8.2 of the [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement)
prohibits using bots or other automated methods to access the service. Doing
this can get your account restricted or permanently removed.

That is the whole risk, stated plainly. Nothing in this codebase reduces it to
zero, and anyone who tells you their tool does is selling something.

## What this tool does not do

There is no anti-detection code here, and none will be added:

- No browser fingerprint spoofing.
- No proxy or IP rotation.
- No randomised user agents or canvas noise.
- No CAPTCHA solving.
- No attempt to mimic human mouse movement beyond ordinary click timing.

Two reasons. The practical one is that LinkedIn's detection does not hinge on
any of that, so the code would add complexity and false confidence without
changing the outcome. The honest one is that evading a platform's enforcement is
a different activity from automating your own account, and this tool is only the
second thing.

What it does instead is keep the volume low enough that the behaviour is roughly
what a person does anyway, and put a human in front of every comment.

## The defaults, and why they are where they are

| Setting | Default | Reasoning |
|---|---|---|
| `engagement.enabled` | `false` | Opting in should be deliberate. |
| `engagement.mode` | `suggest` | Reads and drafts; never clicks. |
| `first_degree_only` | `true` | Engaging with strangers at volume is the pattern that looks like a bot. |
| `likes.max_per_day` | 15 | Below a normal active user's rate. |
| `comments.max_per_day` | 5 | Writing five substantive comments is already a lot. |
| `comments.max_per_author_per_week` | 1 | Stops the tool from following one person around. |
| `min_seconds_between_actions` | 45 to 180, random | A fixed interval is its own signature. |
| `active_hours` | 08:00 to 21:00 | Nobody likes posts at 04:00 every day. |
| `max_actions_per_day` | 25 | A ceiling over everything, so raising one limit does not quietly raise the total. |

Every one of these is checked locally before an action runs. You can raise them.
The further you go, the more the activity stops resembling a person using
LinkedIn, which is the thing the limits are actually for.

## The three modes

**`off`** — nothing happens.

**`suggest`** (the default) — reads your feed, scores each post against your
interests, drafts comments for the ones that clear the bar, and writes
everything to the review queue. It never clicks a like button and never types
into a comment box. Every action waits for `autopilot review approve`.

This is the mode to use. It removes the tedious part, which is finding the posts
worth replying to, and leaves the part that should stay yours, which is deciding
what your name says.

**`auto`** — performs actions without asking. It works. It is also the setting
most likely to cause a problem, and not only with LinkedIn: an automated comment
that misreads a post lands on a real person's feed under your real name, and you
will not know until someone tells you.

If you use `auto`, keep `dry_run: true` for the first several runs and read the
queue to see what it *would* have done.

## What the comment drafter refuses to do

The prompt in `src/linkedin_autopilot/content/prompts.py` bans generic praise
outright, and the draft has to respond to something specific in the post. On top
of that, `content/validators.py` rejects a draft that:

- is shorter than `comments.min_chars` or longer than `comments.max_chars`
- contains a link, a hashtag, or an `@mention`
- strips down to fewer than twelve substantive words once known filler openers
  are removed

The model is also given an explicit way out. `CommentDraft.should_comment` comes
back `false` when the post does not give it anything true and specific to say,
and the prompt tells it that declining is the right answer more often than not.
A scan that queues two comments out of forty posts is the system working.

## Selectors will break

Every CSS selector in `src/linkedin_autopilot/engagement/selectors.py` is a
guess about markup you do not control, and LinkedIn ships front-end changes
constantly. When a scan starts finding nothing:

```bash
autopilot engage scan --debug
```

That writes a screenshot and the page HTML to `state/screenshots/`. Open the
browser the tool launched, inspect the element, and update the entry that no
longer matches. Each field is a tuple of fallbacks tried in order, so add a new
selector rather than replacing the old one.

A broken selector fails loudly with a message naming the file to edit. It never
silently returns an empty feed and reports success.

## If your account gets restricted

Stop immediately:

```bash
autopilot stop
```

Then delete `state/browser-profile/` to clear the session, and do not restart
the engagement half. LinkedIn's appeal process is a human review; an account
that resumes automated activity during an appeal does not do well.

The posting half is unaffected by any of this and can keep running, since it
goes through the API LinkedIn provides for exactly that purpose.
