import { Fragment, useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl, type Line } from "./api";
import { useFeed, usePoll } from "./poll";
import { Linked } from "./linked";
import { Link } from "./route";
import { play } from "./sounds";

/** `visible` false: kept mounted but hidden (a phone on another tab), so the draft survives. */
export function Chat({ visible }: { visible: boolean }) {
  const lines = useFeed(api.transcript);
  const threads = usePoll(api.threads);
  const titles = new Map(threads?.map((t) => [t.id, t.title]));
  const marks = threadMarks(lines);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [lines.length, visible]); // a hidden list loses its scroll: back to the end when shown
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
      await api.say(text);
      play("send");
      setDraft("");
      setError("");
    } catch {
      play("error");
      setError("Couldn't reach Archangel. Is `just agent` running?");
    }
  };

  return (
    <section className="chat" hidden={!visible}>
      <h2>Conversation</h2>
      <div className="lines">
        {lines.map((line) => (
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
          placeholder="Message Archangel"
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

/** Where the conversation moves to another thread: the line that starts each stretch, and that thread.
 * The owner's newest lines may not be filed yet (they are once Archangel reads them, after this list fetched them),
 * so one without a thread is taken to be in the thread of what follows it: its answer. */
function threadMarks(lines: Line[]): Map<number, number> {
  const threads: (number | null)[] = [];
  let next: number | null = null;
  for (let i = lines.length - 1; i >= 0; i--) {
    next = lines[i].thread ?? (lines[i].sender === "owner" ? next : null);
    threads[i] = next;
  }
  const marks = new Map<number, number>();
  let current: number | null = null;
  lines.forEach((line, i) => {
    const thread = threads[i];
    if (thread !== null && thread !== current) marks.set(line.id, thread);
    if (thread !== null) current = thread;
  });
  return marks;
}
