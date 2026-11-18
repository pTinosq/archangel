# Writing styles

How Archangel writes for the owner, as configuration: one profile per audience, over a shared base. M20 in the roadmap. Requested by the owner 2026-11-18.

## Why

Tone used to be a sentence in the orchestrator's prompt ("Warm, calm, discreet, brief…"), true only for talking to the owner, and nothing at all for the messages Archangel writes to other people through the phone, the PC or a connector. Held as files, each audience's style is written, read and tuned on its own, and a new audience is a new file.

## The files

`apps/agent/src/agent/styles/`, one markdown file per profile: TOML settings between `+++` lines, then the style as prose for a model.

```
+++
about = "Discord: how the owner talks to friends there, informal."
via = ["discord"]
+++
You're writing as the owner on Discord: informal, quick, like a friend in a chat.
…
```

| File | For |
|---|---|
| `base.md` | Under every profile. The anti-slop rules: plain words, point first, each thing said once, no padding, openers or closers, no repeated reassurances, no stock AI phrases, the owner's dictated words sent as given. |
| `owner.md` | `owner = true`: the owner, any channel. Short and polite; plain text. |
| `owner-voice.md` | `owner = true`, `via = ["voice"]`: the owner on a call. Written to be heard. |
| `discord.md` | `via = ["discord"]`: informal. |
| `texting.md` | `via = ["whatsapp", "telegram", "signal", "messenger", "imessage"]`: casual, like texting someone you know. |
| `semi-formal.md` | `via = ["email", "gmail", "outlook", "slack", "teams", "linkedin"]`: polite, direct. |
| `default.md` | Nothing else fits (an unknown person or app). Neutral, friendly, brief. |

Settings: `about` (a line: who it is for), `owner` (true or false), `people` (names), `groups` (the owner's groups of people, below), `via` (channels or apps, as the app or channel calls itself: `sms`, `voice`, `panel`, `discord`, `email`). Names match without regard to case. The file name is the style's name.

## Choosing

`Styles.choose(Audience(owner, person, via))`:

1. A profile fits when everything it names fits: its `owner` if set, one of its `people`, one of its `groups`, one of its `via`. Naming nothing fits all, which only `default` may.
2. Of those that fit, the most specific wins: one naming the person, then their group, then one naming the owner, then one naming where. Between two, whichever names more of the things the audience has; still tied (someone in two groups), the first by file name.
3. None fit: `default`.

So the owner on a call gets `owner-voice` (owner and where) over `owner`; the owner on Discord gets `owner`, not `discord` (the owner outranks the app); anyone else on Discord gets `discord`.

## Groups

The owner talks to buckets of people: uni friends, climbing friends, family. A profile with `groups = ["uni friends"]` is for everyone in that group, in any app (or only in some, with `via`).

Who's in a group isn't in the file: it's the owner's life, so it's the context twin's. A group is a node in the twin (an alias works too: "uni lot"), and a person is in it when their node links to it, or it to them. The twin's writer makes the node and the links when the owner says so ("Sam and Priya are my uni lot"), or the owner links them on the Context twin page. `writing_style` looks the person up in the graph in code, no model call: their node (so an alias, "Sam Taylor", finds a profile for "Sam") and every node linked to it. Only the ones a profile names matter.

The twin holds who's in the group, never which style they get: that's the profile's.

## Refused at start

Two profiles that could win the same audience (both for the owner, both for Discord, both for the same group) fail at start, so the choice never depends on file order. Two groups can't be checked (who's in both is the twin's), hence the tie rule above. So do a typo in a key, a missing `about`, an empty text, and a missing base or default: a style that quietly never matches is worse than a refusal.

## Where it is applied

- **The orchestrator's own replies.** Each turn, the style for the owner on the channel the reply goes back by (`sms`, `voice`, `panel`…) is added to the situation. Chosen in code, no model decision. `send` to another channel says to ask `writing_style` first.
- **Anything written to someone else.** `writing_style(to, via)`, a tool given to the orchestrator, both pilots and the connectors helper (as `ask_twin` is). `to` is `owner` or a name, `via` the app. It returns the base and the chosen profile. The orchestrator's instructions say to name who and where in a task's goal; the pilots' and helper's prompts say to call it before typing a message, post or email. The model chooses to ask, so a task that skips it gets no style: the cost of keeping tone out of every prompt.
- **Not styled:** reports (the reporter's own layout and prose), summaries, and the twin's pages.

## Adding one

1. Write `styles/bluesky.md` with `about`, `via = ["bluesky"]` and the prose.
2. `just style Sam bluesky` shows the one chosen and the text a model would get; `just style` lists them all.
3. Run the tests (`tests/test_styles.py` covers the selection rules, and the starter profiles' routing; add a line for the new one).

No code changes. To tune one, edit its file and look again.

The owner's own styles (naming real people or accounts, which can't go in this public repo) go the same way into `apps/agent/data/styles/` on the volume (Railway: `/app/apps/agent/data/styles/`). A file there adds a style or replaces the built-in of its name; a style for one person is `people = ["Sam"]`; for a group, `groups = ["uni friends"]`, no names needed, so a group's style can live in the repo. Read at start.

## Files or the panel's Prompts tab?

The built-in profiles live as files, with the Prompts tab left for later. The files hold more than text: who each profile is for is structure the selection and its start-up checks depend on, and it belongs in version control with the tests that pin it. The tab's store can hold a prompt's text only. Files also get reviewed in PRs, and Archangel's `improve` can change them. But the tab's strength is tuning live: an edit applies from the next run, with a restore-default button, no deploy. The right end state is both: the file is the default and its settings, and the tab edits only the prose (`DEFAULTS["style discord"]`, read through `Prompts.live`), as it does for every other prompt. That is a small change on top of this one, not built here: it needs a decision on the owner's own folder (a file there and a tab edit both replace a built-in), and on listing a dozen styles among the twelve prompts. Until then, `data/styles/` is the way to tune without a PR.

## Later

- A real-model eval, like the twin's and router's: drafts for a made-up audience, checked in code for what the style forbids (openers, closers, emoji, length) and by a judge for the rest.
- The Prompts tab, as above.
