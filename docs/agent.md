# Agent in detail

What `apps/agent/` does, beyond the one-line roles in CLAUDE.md. Threads, the context twin and writing styles have docs of their own ([threads.md](threads.md), [twin.md](twin.md), [styles.md](styles.md)); the panel too ([panel.md](panel.md)).

## Models

`models.py`, `openrouter(name, timeout=120)`:

- **Only `TRUSTED` hosts**, in the owner's order, each falling back to the next: an open model's cheap hosts can serve gibberish. A new model may need its host added.
- **A stalled call is abandoned and retried**, not left blocking the one conversation. Deep research gets 15 minutes.
- **The orchestrator runs with `think=False`**, reasoning as little as its model allows: DeepSeek otherwise thinks for up to a minute a call, and everything the owner says waits behind it. No effort level or token cap bounds it. The background agents keep reasoning.

## Channels

`Channel`: `name`, `shows` (what it can show, listed to the orchestrator: a call can't show a picture), `owner` (the owner's address there), `start(deliver)` (the owner's messages, or a `Notice`), `send(message)`. Channels only move messages.

- **`cli.py`**: the terminal, `/image <path> [text]`. Only when there is one.
- **`panel.py`**: the panel's channel and its API, routes async (SQLite). Transcript, tasks, steps, images, prompts; `/api/activity` (server-sent events of live activity); `/api/connectors` (the services, never their secrets); `/api/tools` (the quick tools, on or off and their settings; a secret's value never leaves; `PUT` changes one); `/api/twin` (the graph, a node's page, links and history, and the owner's edits: nodes, names, aliases, links); `/api/schedule` (what's to come); `/api/threads`, `/api/threads/{id}`; `/api/messages` (text, pictures as base64 (up to 4, 5 MB each, JPEG, PNG, GIF or WebP; stored with the transcript like any incoming image), and an optional `thread`); `/api/version` (the commit running and since when).
- **`login.py`**: sign in with GitHub, the owner only; a signed session cookie; afterwards, back to the page asked for (a texted report link).
- **`twilio.py`, texts** (`/twilio/sms`): checked against Twilio's signature, other numbers ignored. Replies by Twilio's API; screenshots as a private link (UK numbers can't send pictures).
- **`email.py`** (`/email/inbound`, M21): Archangel's own address, through Resend. The webhook (Svix-signed, refused after 5 minutes) carries only the email's id; it's answered at once and the email (text, or its HTML as text; headers; attachments) fetched from Resend's API after. Each email is taken once (Resend retries).
  - **Who is read:** only senders on the **Email senders** list (the Connectors page; the `inbox` quick tool's `senders` setting, addresses separated by commas or new lines, matched on the address alone, any case). An empty list, or the tool switched off, reads no one. Anyone else's mail is dropped as soon as its sender is known: not stored, routed, shown to a model or taken as an instruction (only a log line). The list is read at each email, so a change applies at once. `ARCHANGEL_OWNER_EMAIL` doesn't grant reading: its first address is where Archangel writes unprompted, and the list must hold it too.
  - **Whose:** a listed sender is the owner's own address (the owner puts only theirs there), so it is a message if Resend's own checks on receipt pass DKIM or DMARC (both mean the From domain vouched for it; SPF alone doesn't): subject and text, a reply without the history it quotes, pictures up to 4, 5 MB each, other files named (for M19). A listed address failing the checks is a `Notice`: not from the owner, so it can approve nothing.
  - **Forwards:** `forwarded_from(subject, text, own)` reads the original sender from the content, not one provider's format: the first From line (within 15 lines) after a "Forwarded message" marker (Gmail, Yahoo, Apple Mail), or after Outlook's "Original Message" or with no marker at all when the subject says "Fwd:" (Outlook's marker is also a reply's quote, so alone it counts for nothing). The address is taken from the line whatever its shape (`Name <a@b>`, `a@b`, `[mailto:a@b]`). Never guessed: it's `None` when it isn't a forward (and for a reply in a chain), when the line has no address, or when it's Archangel's own. It's kept with the email (`reply_to`), the orchestrator told "Forwarded from …: the reply goes to them". **The answer to a forward goes to that original sender** (subject "Re:" without the "Fwd:", no In-Reply-To: the forwarder's chain isn't theirs); when unresolved it goes to whoever sent the mail, in its chain. That is mail to someone else with no OK step: a reply to an email the owner forwarded asking for one.
  - **Chains:** each email is kept by its Message-ID (table `email`). Its address is that Message-ID, so the answer goes back In-Reply-To it, "Re: …", and the thread it was answered in is noted: the next email in the chain (its References) goes to that thread without the router. Mail Archangel starts goes to the owner's first address, its first line the subject; a reply to that one is routed like any message (Resend doesn't say what Message-ID it sent).
  - **To anyone else:** the helper's `send_email` (`outbox`), after the owner's OK with the draft shown, like any connector call that isn't read-only. The orchestrator gets there through `use_connectors`; `shows` tells it so.
- **`twilio.py`, calls** (`/twilio/voice`): Twilio's ConversationRelay does speech and turn-taking; Archangel trades text with it over a WebSocket (`/twilio/voice/relay`, signed the same way).
  - Speech split at a breath (under a second apart) is joined into one thing said, so it gets one reply.
  - After `Message.ends` (the orchestrator's `hang_up`) it ends the call once the words have had time to be spoken (Twilio doesn't say when it's done), unless the owner speaks first.
  - On a call, everything is said on it. Otherwise it rings only when asked for a call back (`report_via voice`), at most 3 times in 10 minutes, and texts after a hang-up, but not replies to what was said on that call, like "Goodbye." (`Message.answering`).
  - Calls it makes connect at once (the message is said on pickup); answering-machine detection runs in the background, and a voicemail gets the message after the beep. How a call went (voicemail, no answer) is a `Notice` to the orchestrator, which decides what to text.
  - `shows` says whether a call is live.

## Web, links, location

- **`web.py`**: local (127.0.0.1, Host-checked) unless GitHub sign-in is set; then public on `$PORT`, trusting the proxy's headers, and serving the built panel. Pages asked for at another address (Railway's own) go to the same page at the public address, so sign-in lives on one domain (not `/health` or `/twilio/`).
- **`links.py`**: signed links that open without sign-in and expire after a day, only with a public address: screenshots (`/screenshots/…`) for texts (on a call, an approval's screenshot is texted this way and the call says so), and the location page.
- **`location.py`**: the phone stays at home, so `owner_location` gives the owner's last shared place, how long ago, and a link (`/where/…`) whose page shares the browser's location once, on opening. Nothing runs on the owner's phone. The address comes from OpenStreetMap (Nominatim), is kept in the store, and arrives as a `Notice` where the link was asked from.

## Orchestrator

`handle(event)`, one at a time, in the event's thread (`threads.route`, beside the twin's reader: both model calls), seeing that thread's history only; what it starts and says is filed under it.

### Tools

- `start_task(goal, report_via?, on?)`: a device task, `on` the phone or the PC.
- `use_connectors(goal, report_via?)`: a background task in the owner's connected services; it sees which are connected.
- `research(question, deep?, report_via?)`: a background task. `deep` only when the owner asked (`owner_asked`), once they've agreed to its cost.
- The quick tools' functions (`tfl_status`, `weather`, `convert_currency`, `calculate`…, from `tools/`, only while on): answers inline. The order to try: a tool, then the web, then research, a device last.
- `search_web(question)`, `read_page(url)`: answers inline; read what a result only points to.
- `installed_apps(on?)`: what's installed, to choose between the web and an app.
- `answer_task`: a waiting task's answer, or the owner's word to a running one. `redirect_task(id, channel)` ("text me when it's done"), `cancel_task`.
- `report_visibility(id, public?)`: says whether a report is public, or changes it; public only when the owner asked (`owner_asked`).
- `read_report(report)`: a finished report's full text, by id or link (`reports.text_of` reads it back from the stored HTML, so every existing report works). A private report only for the owner's own request (`owner_asked`).
- `send(channel, text, screenshot?)`; `hang_up` (on a call, after this reply).
- `owner_location`: for "near me"; tasks get the address typed.
- `do_later`, `cancel_later`, `note_progress`: below.
- `remember`, `forget`: the owner's words, to the twin's writer at once. `ask_twin`.
- `writing_style`: how to write to someone else; its own replies come with their style.
- `search_threads`, `read_thread`: another thread, when the owner refers to one.
- `improve(request, pr?)`: Archangel changing itself (M14), a background `code` task. `merge(pr)` deploys. Both only when the owner asked (`owner_asked`): their own message, or a schedule set with one ("at 19:10 ask Claude to…"; `Job.said` is only ever the owner's words). Approvals still need them now (`from_owner`).

### What it sees, every turn

The twin's root page; before answering anything the owner says, what the twin's reader finds bears on it (a few seconds: each page opened is a model call). The owner's time (`Europe/London`), its thread (and what it carries on from), the other recent threads, the quick tools that are on, the channels, devices (online, offline, woken when needed; a PC that never connected isn't listed), the task list (public reports and other threads' tasks marked) and what's scheduled.

### Replies

To `event.reply_to`, or none: it may `stay_quiet` (an output, ending the run with nothing sent) on anything not from the owner, like a repeat's check that found no change. The owner always gets an answer. Approvals (yes or no) only when `event.from_owner`: it never decides one for the owner.

### Later (M13)

- `do_later(what, at? | in_minutes?, every?, report_via?, max_runs?)`: "in 15 minutes get me an Uber" wakes it with `what` when due, in this thread (a `Scheduled` event). `at` is the owner's local time; code does the arithmetic. `every` is cron in the owner's time ("every weekday at 7"). A repeat stops after `max_runs`, default 50, at most 200. Its reply names anything already upcoming in the thread with the same `what`, so a duplicate can be cancelled.
- `cancel_later`: a one-off, or a repeat with all its runs. The owner may cancel any; the orchestrator on its own only what was scheduled in its thread.
- A repeat's wake-up says its run number and offers `cancel_later` once what it waited for has happened. The last run says it was the last, so the owner is told it stopped.
- A repeat's runs are silent in code, whatever the model writes, except the one that ends it (`cancel_later` on its own job) and the last: it reports once, never a "no change yet".
- A repeat's wake-up carries what its last run found: the task it started (goal, status, report, trimmed) and its noted progress, so it can tell nothing's new and hand a new pilot the way there (`Orchestrator.wake`).
- `note_progress(progress)`: in a scheduled run, the line the panel's Scheduled list shows (`Job.progress`); sends nothing.

`schedule.py` holds the rows: `add(due, what, said, report_to, thread, every?, max_runs?)`, `get`, `cancel`, `upcoming`, `started(id, task)` (a run started this task: `Job.last_task`, shown to the next run), `watch(fire)`. `watch` sleeps until the next due or a change. It marks each fired, or moves a repeat to its next time (`following`, cronsim) and counts the run, before firing, so a crash never does it twice. A repeat's last run (`DEFAULT_RUNS` 50, `MAX_RUNS` 200) is marked fired like a one-off, so no loop runs forever. Whatever came due while down fires on start, once. Times are absolute and shown both ways (`when`: "Wed 18 Nov 08:00, in 11 h"); `parse(at, in_minutes, every)`. No Temporal: one owner's timers are rows and a sleeping loop.

## Tasks

`tasks.py` briefs each new device or connectors task from the context twin first; a step shows the briefing. Not research: its question goes to a web search, and the owner's life needn't.

## Research and reports

- **`research.py`**: the web, never the phone. `search` and `read` are Tavily search and extract (results with sources, or a page as text, in seconds; the orchestrator answers from them). `research` is Perplexity's Pro Search (`sonar-pro-search`, via OpenRouter): under a minute, ~3p, sources listed. `deep` is Sonar Deep Research: ~4 minutes, ~60p, an 80k-character essay whatever it's told.
- **`reports.py`**: topic and material in, `Report(tldr, title, pages)` out (the main model, a few pence). The model writes the pages; the layout is fixed: a cover (title, tldr, date), then portrait pages like a PDF's, phone-shaped (9:16), wide tables scrolling inside, in the panel's look with system fonts (the page may load nothing). Saved as finished HTML, so a new style applies to new reports only. A research task's report is its link (`/reports/{id}`, behind sign-in unless public: `Task.public`, `Web.share`; a CSP lets nothing in it run or load) and the tldr, which the orchestrator texts. What research found is a step.
- **Pictures**: the reporter has `search_images` (Tavily, with descriptions), `page_images` (a page's pictures) and `add_image(source, alt)`, where `source` is any web address the model chose or `owner N` for a picture the owner sent in the task's thread (latest 4, while they last). It sees each picture it adds and uses it as `<img src="img1.png">`; at most 8. No source is fixed in code. Anything that can't be had (not public, not a picture, over 4 MB, gone) is a note to the model, and the report carries on without. Fetching is safe against the model's addresses: http(s) only, every address (and redirect) must resolve to the public internet, no credentials sent, the type read from the bytes (jpeg, png, webp, gif; never SVG). Saved with the page in `data/pages/{id}-images/` (no expiry), served at `/reports/{id}/images/{name}`; any `<img>` that isn't an added picture is dropped from the page. A picture opens exactly when its report does (`Web.share` covers both paths); `Cache-Control: private`, so a proxy keeps none. A `reporter` prompt edited on the Prompts tab keeps its old text: reset it to get the picture instructions.

## Connectors

`connectors.py`: the owner's online services as MCP servers (`pydantic_ai.mcp.MCPToolset`), added and signed in to from the panel.

- **Hosted ones** (Notion; Slack with the owner's own Slack app; any other) sign in through the MCP SDK's `OAuthClientProvider`. Dynamic registration where allowed, afresh at each sign-in: an old registration may be revoked or name an old address. It refreshes tokens. The owner comes back to `/connectors/callback` with the issuer (`iss`, RFC 9207): the SDK rejects a code without it when the server advertises it, as Notion does.
- **Gmail and Calendar**: Archangel signs in to Google itself and runs `workspace-mcp` (`uvx`, stdio) on that sign-in. Each Google account runs its own, on its own credentials folder, only for the length of a run (`keep_alive=False`: FastMCP's default left one running after every task, ~260 MB each). The credentials file is written (with the token's expiry) only at a new sign-in; after that it's workspace-mcp's, keeping the token it refreshed (rewritten each run, every call met a 401).
- **Gmail tidying** (`gmail.py`, a `process_tool_call` hook): inline `data:` images cut out, and the message's links ("text → URL") listed after it, from its whole HTML (workspace-mcp exports it to a file, read and deleted). Its 20,000-character cut can land before a shop's button, and plain-text parts often have no links.
- **Failing**: one that won't connect drops out of the run and shows "needs sign-in".
- **Profiles**: one connector per account, in a profile ("work", "personal"); the agents see them by profile and choose. Each has its own settings (a work Google client may live in the company's Google Cloud).

## Quick tools (M15)

`tools/`: live data the orchestrator answers from inline, no device.

- `tool.py`: `Tool` (ABC): `name`, `title`, `about` (one line, for the owner and the orchestrator's list), `settings` (`Setting(key, label, secret?, required?)`), `functions()` (async, return text), `setting(key)`, `get(url, **params)`. The functions' names are the orchestrator's tool names and their docstrings its description, so say what each is for and not for. `ToolError`: a message the model acts on.
- `__init__.py`: `Tools(store, client?, kinds=BUILT_IN)`. Add a tool: subclass `Tool`, list it in `BUILT_IN`; the panel shows it by itself. `all()` and `configure(name, enabled?, settings?)` for the panel; `active` (on, and every required setting set; on by default); `describe()` (the orchestrator's list each turn); `toolset` (only active tools' functions). A source that fails is answered as "isn't answering: try the web", never raised. The owner's state is the store's `tools` table.
- Built in, all free and keyless: `tfl.py` (line status and why, journeys (an unclear place resolves to the best match, said), arrivals; optional `app_key`), `weather.py` (Open-Meteo: now, hourly, days, sunrise and sunset; optional default `place`), `currency.py` (Frankfurter), `dates.py` (the time anywhere, UK bank holidays, days between, exact arithmetic with an AST evaluator that accepts only numbers and operators), `places.py` (Open-Meteo geocoding, shared). `inbox.py` ("Email senders", M21): no functions, only the `senders` setting the email channel reads (`Tools.setting(name, key)`, empty while off; `addresses(text)` parses it); it's not in `describe()`.

## Self-improvement (M14)

`coder.py`, `Coder(github(token))`. `change(request, branch, record, found?, run?)` starts `.github/workflows/improve.yml` (Claude Code in GitHub Actions on the owner's Claude subscription; its token is a repo secret, never here), finds its run by name (`run-name` is the branch), follows it, and reads its `result` artifact: the PR and a summary, or a question's answer, or Claude's error when it failed. A run with zero turns (Claude never started: a usage limit or an outage) is dispatched once more after a minute, said in the steps. `found` hears the run's id as soon as it's found (kept as the task's `github_run`); `run` follows one already going: after a restart, an interrupted code task follows its run to the result instead of being reported interrupted. `branch(pr)` carries a PR on; `merge(pr)` squashes, and the orchestrator's `merge` lists the tasks running, which the deploy cuts off (code tasks carry on). The request, run and PR are public.

## In the cockpit

Research's `search` and `read`, every connector call and every quick tool call are `service` calls; each connector is a part of its own.

## Devices

- **Pilot** (`devices/pilot.py`): `Pilot(model, Kit(device, vision, guard, investigator), Platform)`; a `Platform` (`phone.py`, `pc.py`) brings the tools, prompt and tidying up. `open_app` takes a name, or part of one only one app has; a miss lists the nearest few. `done` is its report to Archangel: it messages the owner only if the goal says to, and finishes once it has the answer. No rules for stuck: it judges its own progress, asks for a second opinion or gives up; 120 steps is only a runaway backstop. It notes findings as it goes, since old screens are forgotten.
- **Phone** (`devices/phone.py`): `close_app` is code (launch, recents, swipe its card up, home), no model guesswork. `press unlock` swipes up past a lock screen without a PIN. `set_location` fakes the GPS, only for apps that just use it ("near me"). `done` goes home unless `leave_open`.
- **PC** (`devices/pc.py`): `type_text` types at the cursor and line breaks never submit. `scroll(direction, within?)` is the wheel over a described area. `press(keys)`: all but moving around and selecting are checked like a click. `unlock`: the bridge types the PIN it keeps; locked again when the run ends or pauses. `run_command(command)`: PowerShell as the signed-in user; the pilot sees the exit code and up to 4,000 characters each of output and errors, not the screen (it looks next). Checked by the guard from its text alone; an approval shows the owner the command.
- **Relay client** (`devices/relay.py`): positions are screen fractions, turned into pixels with the latest screenshot's size, so rotation is fine. Screenshots are JPEG: the phone's half-scale at quality 30 (Jev rejects WebP; 30 locates as well as 80), the PC's longer side 1536 at 60 (desktop text is small; Jev rejects a side over about 1568). Offline (503): waits up to 60 s, then resends; a PC whose network card it knows is woken by the phone (Wake-on-LAN) and waited for 150 s. Lost mid-command (502): resends only read-only commands. Each hello is kept in the store.
