# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
One owner: the person who runs Archangel and signs in with GitHub. They use the control panel on desktop and phone about equally: on desktop to watch and debug the agent at work, on the phone to chat, approve actions and read reports on the go. Reports are read by the owner alone, usually on the phone, opened from a texted link.

## Product Purpose
Archangel is a personal AI agent that does things for its owner: it operates a real Android phone, searches and researches the web, and works in the owner's connected services (Gmail, Calendar, Notion, Slack). The owner talks to it by text, voice call or the control panel. The panel is mission control: watching a capable agent work on the owner's behalf, live, precise and trustworthy. Success is the owner trusting what it does and stepping in only when it asks.

## Positioning
It acts on a real phone, the way a person would, so any app is in reach; nothing that sends, pays, deletes or changes settings happens without the owner's yes.

## Operating Context
- Panel pages: Chat (its own page on phones), Tasks (list, detail with phone screens and steps, approve/decline/answer), Cockpit (live graph of who is calling whom, plus an activity timeline), Prompts (edit each agent's prompt), Connectors (add and sign in to services, grouped by profile such as work and personal).
- Pages live at real paths: /chat, /tasks, /tasks/{id}, /cockpit, /prompts, /connectors.
- Reports: a research task's write-up at /reports/{id}, a cover then portrait 9:16 pages, read on a phone. Behind sign-in, with a strict content policy: they may load nothing, so system fonts only.
- Approvals arrive by text or call and can be given in the panel.

## Capabilities and Constraints
- React + Vite + plain CSS panel, served by the agent; polls its API each second, activity streamed live.
- Phone breakpoint 760px: one column at a time.
- The owner values small, clean, low-slop code and short copy.

## Brand Commitments
- Name: Archangel. No logo or emblem (the owner rejected one); the name stands alone.
- Voice: short, direct, plain.

## Evidence on Hand
Real tasks, steps, screens and transcripts in the local store. No customers, testimonials or metrics: it is a personal tool, and none should be invented.

## Product Principles
1. Trust through visibility: show what the agent is doing and why, live.
2. The owner decides: every consequential action waits for them, and that moment is unmistakable.
3. Calm until it matters: quiet by default, loud only when the owner is needed.
4. Works as well on a phone as on a desk.
