import { Fragment, useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl, type Line, type Thread } from "./api";
import { useFeed, usePoll } from "./poll";
import { Linked } from "./linked";
import { Link } from "./route";
import { play } from "./sounds";

/** `visible` false: kept mounted but hidden (a phone on another tab), so the draft survives. */
export function Chat({ visible }: { visible: boolean }) {
  const lines = useFeed(api.transcript);
  const threads = usePoll(api.threads);
  const titles = new Map(threads?.map((t) => [t.id, t.title]));
  const threadOf = threadsOf(lines);
  // Main is the whole conversation, the router placing each message; choosing an open thread shows only it, and what's
  // said there stays there. Closed ones are the Threads page: one that closes while chosen goes back to Main.
  const [tab, setTab] = useState<number | null>(null);
  const listed = (threads ?? []).filter((t) => t.open).sort((a, b) => b.id - a.id);
  const current = listed.find((t) => t.id === tab);
  const shown = current ? lines.filter((_, i) => threadOf[i] === current.id) : lines;
  const marks = current ? new Map<number, number>() : threadMarks(lines, threadOf);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [lines.length, visible, tab]); // a hidden list loses its scroll: back to the end when shown
  const heard = useRef<number>(undefined);
  useEffect(() => {
    const last = lines.at(-1);
    // Not for the history on load: only a reply that arrives while the panel is open.
    if (heard.current !== undefined && last && last.id > heard.current && last.sender === "archangel") play("receive");
    if (lines.length) heard.current = last!.id;
  }, [lines.length]);

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      await api.say(text, current?.id);
      play("send");
      setDraft("");
      setError("");
    } catch {
      play("error");
      setError("Couldn't reach Archangel. Is `just agent` running?");
    }
  };

  const close = async (thread: Thread) => {
    try {
      await api.closeThread(thread.id);
      setTab(null);
      setError("");
    } catch {
      play("error");
      setError("Couldn't close the thread.");
    }
  };

  return (
    <section className="chat" hidden={!visible}>
      <h2>Conversation</h2>
      <div className="chat-bar">
        <select value={current?.id ?? ""} onChange={(e) => setTab(e.target.value ? Number(e.target.value) : null)} aria-label="Thread">
          <option value="">Main: everything</option>
          {listed.map((thread) => (
            <option key={thread.id} value={thread.id}>
              #{thread.id} {thread.title}
            </option>
          ))}
        </select>
        {current && (
          <button onClick={() => close(current)} title="Close it: summarised and filed under Threads">
            Close
          </button>
        )}
      </div>
      <div className="lines">
        {current && !shown.length && <p className="quiet">Nothing said here yet.</p>}
        {shown.map((line) => (
          <Fragment key={line.id}>
            {marks.has(line.id) && (
              <Link to={`/threads/${marks.get(line.id)}`} className="thread-mark">
                #{marks.get(line.id)} {titles.get(marks.get(line.id)!) ?? ""}
              </Link>
            )}
            <Said line={line} />
          </Fragment>
        ))}
        <div ref={end} />
      </div>
      {error && <p className="error">{error}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={current ? `Message in #${current.id}` : "Message Archangel"}
          enterKeyHint="send"
        />
        <button type="submit" data-sound="">
          Send
        </button>
      </form>
    </section>
  );
}

/** One thing said, by the owner or Archangel, on any channel. */
export function Said({ line }: { line: Line }) {
  return (
    <div className={`line ${line.sender}`}>
      <span className="via">{line.channel}</span>
      <p>
        <Linked text={line.text} />
      </p>
      {line.images.map((name) => (
        <a key={name} href={imageUrl(name)} target="_blank">
          <img src={imageUrl(name)} alt="" onError={hideExpired} />
        </a>
      ))}
    </div>
  );
}

/** The thread each line is in. The owner's newest lines may not be filed yet (they are once Archangel reads them,
 * after this list fetched them), so one without a thread is taken to be in the thread of what follows it: its answer. */
function threadsOf(lines: Line[]): (number | null)[] {
  const threads: (number | null)[] = [];
  let next: number | null = null;
  for (let i = lines.length - 1; i >= 0; i--) {
    next = lines[i].thread ?? (lines[i].sender === "owner" ? next : null);
    threads[i] = next;
  }
  return threads;
}

/** Where the conversation moves to another thread: the line that starts each stretch, and that thread. */
function threadMarks(lines: Line[], threads: (number | null)[]): Map<number, number> {
  const marks = new Map<number, number>();
  let current: number | null = null;
  lines.forEach((line, i) => {
    const thread = threads[i];
    if (thread !== null && thread !== current) marks.set(line.id, thread);
    if (thread !== null) current = thread;
  });
  return marks;
}
