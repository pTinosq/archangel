import { Fragment, useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl, type Line, type Picture } from "./api";
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
  const [pictures, setPictures] = useState<Attached[]>([]);
  const [sending, setSending] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [error, setError] = useState("");
  const chooser = useRef<HTMLInputElement>(null);
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

  const attach = async (files: File[]) => {
    const images = files.filter((file) => file.type.startsWith("image/"));
    if (!images.length) return;
    try {
      const added = await Promise.all(images.map(attachable));
      const all = [...pictures, ...added];
      setPictures(all.slice(0, MAX_PICTURES));
      setError(all.length > MAX_PICTURES ? `Up to ${MAX_PICTURES} pictures at a time.` : "");
    } catch {
      play("error");
      setError("Couldn't read that picture.");
    }
  };

  const send = async () => {
    const text = draft.trim();
    if ((!text && !pictures.length) || sending) return;
    setSending(true);
    try {
      await api.say(text, current?.id, pictures.map(({ data, media_type }) => ({ data, media_type })));
      play("send");
      setDraft("");
      setPictures([]);
      setError("");
    } catch (e) {
      play("error");
      setError(e instanceof TypeError ? "Couldn't reach Archangel. Is `just agent` running?" : `Not sent: ${(e as Error).message}`);
    } finally {
      setSending(false);
    }
  };

  return (
    <section
      className={dropping ? "chat dropping" : "chat"}
      hidden={!visible}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDropping(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropping(false);
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDropping(false);
        attach([...e.dataTransfer.files]);
      }}
    >
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
      {pictures.length > 0 && (
        <ul className="attached">
          {pictures.map((picture, i) => (
            <li key={i}>
              <img src={picture.url} alt="" />
              <button
                type="button"
                aria-label="Remove picture"
                onClick={() => setPictures((now) => now.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          ref={chooser}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            const files = [...(e.target.files ?? [])];
            e.target.value = ""; // choosing the same file again still counts
            attach(files);
          }}
        />
        <button type="button" className="attach" aria-label="Attach pictures" onClick={() => chooser.current?.click()}>
          +
        </button>
        <textarea
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPaste={(e) => {
            const files = [...e.clipboardData.files];
            if (!files.some((file) => file.type.startsWith("image/"))) return; // text pastes as text
            e.preventDefault();
            attach(files);
          }}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a new line. Not mid-composition (an IME's Enter picks a word).
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={current ? `Message in #${current.id}` : "Message Archangel"}
          enterKeyHint="send"
        />
        <button type="submit" data-sound="" disabled={sending}>
          Send
        </button>
      </form>
    </section>
  );
}

const MAX_PICTURES = 4; // the agent's MAX_IMAGES
const MAX_BYTES = 5 * 1024 * 1024; // the agent's MAX_IMAGE_BYTES
const MAX_SIDE = 2048;
const SENT_AS_IS = ["image/jpeg", "image/png", "image/gif", "image/webp"];

/** A picture ready to send, and a `url` to show it by until then. */
type Attached = Picture & { url: string };

/** A file as the agent takes it: as it is when it can, otherwise (too big, or a type models don't read) redrawn as a
 * JPEG small enough. Throws on what the browser can't read. */
async function attachable(file: File): Promise<Attached> {
  if (SENT_AS_IS.includes(file.type) && file.size <= MAX_BYTES) {
    const url = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    return { url, data: url.split(",")[1], media_type: file.type };
  }
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const url = canvas.toDataURL("image/jpeg", 0.85);
  return { url, data: url.split(",")[1], media_type: "image/jpeg" };
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
