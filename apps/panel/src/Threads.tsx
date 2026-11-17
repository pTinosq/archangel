import { useCallback } from "react";
import { api, type Thread, type ThreadDetail } from "./api";
import { Said } from "./Chat";
import { useNarrow } from "./narrow";
import { usePoll } from "./poll";
import { Linked } from "./linked";
import { Link } from "./route";

/** The conversation's threads, one per topic, newest first; `selected`: the one in the address, /threads/12. */
export function Threads({ selected, select }: { selected?: number; select: (id?: number) => void }) {
  const threads = usePoll(api.threads);
  const narrow = useNarrow();
  // As with tasks: a desktop always shows one beside the list; a phone shows the list, or the one picked from it.
  const shown = threads?.find((t) => t.id === selected) ?? (narrow ? undefined : threads?.[0]);

  return (
    <>
      {!(narrow && shown) && (
        <section className="task-list thread-list">
          <h2>Threads</h2>
          {threads?.length === 0 && <p className="quiet">None yet. Each topic you raise with Archangel becomes one.</p>}
          <ul>
            {threads?.map((thread) => (
              <li key={thread.id} className={thread.id === shown?.id ? "selected" : ""} onClick={() => select(thread.id)}>
                <div>
                  <span className="id">#{thread.id}</span> <State thread={thread} />
                  <time>{ago(thread.updated)}</time>
                </div>
                <div className="goal">{thread.title}</div>
                <Size thread={thread} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {shown ? (
        <ThreadPage key={shown.id} thread={shown} back={narrow ? () => select() : undefined} />
      ) : (
        !narrow && <section className="detail thread" />
      )}
    </>
  );
}

function State({ thread }: { thread: Thread }) {
  return <span className={`status ${thread.open ? "open" : "closed"}`}>{thread.open ? "open" : "closed"}</span>;
}

/** How full its context is: a thin bar, acid until it's nearly full; at full it carries on in a new thread. */
function Size({ thread, labelled }: { thread: Thread; labelled?: boolean }) {
  const share = Math.min(1, thread.size / thread.full);
  return (
    <div className={share > 0.8 ? "size nearly-full" : "size"} title={`${thousands(thread.size)} of ${thousands(thread.full)} tokens`}>
      <span style={{ width: `${share * 100}%` }} />
      {labelled && (
        <em>
          {thousands(thread.size)} of {thousands(thread.full)} tokens
        </em>
      )}
    </div>
  );
}

function ThreadPage({ thread, back }: { thread: Thread; back?: () => void }) {
  // Polled on its own: what was said in it grows while it's open.
  const load = useCallback(() => api.thread(thread.id), [thread.id]);
  const detail: ThreadDetail | undefined = usePoll(load);

  return (
    <section className="detail thread">
      <div className="head">
        {back && (
          <button className="back" onClick={back}>
            ‹ Threads
          </button>
        )}
        <h2>
          #{thread.id} {thread.title}
        </h2>
        <p>
          <State thread={thread} />
          <span className="quiet">
            started {ago(thread.created)} · last said in {ago(thread.updated)}
          </span>
        </p>
        <Size thread={thread} labelled />
        {thread.continues && (
          <p className="quiet">
            Carries on from <Link to={`/threads/${thread.continues}`}>#{thread.continues}</Link>, which was full.
          </p>
        )}
        {thread.summary ? (
          <p className="report">
            <Linked text={thread.summary} />
          </p>
        ) : (
          <p className="quiet">Summarised once it's finished: you wrap it up, or it's quiet for a few hours.</p>
        )}
        {!!detail?.tasks.length && (
          <p>
            Tasks:{" "}
            {detail.tasks.map((task) => (
              <Link key={task.id} to={`/tasks/${task.id}`} className="task-link">
                #{task.id} {task.goal}
              </Link>
            ))}
          </p>
        )}
      </div>
      <div className="lines said">
        {detail?.lines.map((line) => <Said key={line.id} line={line} />)}
        {detail && !detail.lines.length && <p className="quiet">Nothing said in it yet.</p>}
      </div>
    </section>
  );
}

const thousands = (tokens: number) => (tokens < 1000 ? `${tokens}` : `${(tokens / 1000).toFixed(1)}k`);

function ago(time: number): string {
  const minutes = Math.round((Date.now() / 1000 - time) / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`;
  const days = Math.floor(minutes / (24 * 60));
  return days === 1 ? "1 day ago" : `${days} days ago`;
}
