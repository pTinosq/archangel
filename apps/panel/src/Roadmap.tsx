import { useState } from "react";
import { api, type RoadmapMilestone } from "./api";
import { usePoll } from "./poll";

const FILTERS = { all: "All", open: "Open", done: "Done", later: "Later" } as const;
type Filter = keyof typeof FILTERS;

/** The roadmap: a view of docs/roadmap.md, parsed by the agent on every request. Nothing here knows a milestone. */
export function Roadmap() {
  const roadmap = usePoll(api.roadmap);
  const [filter, setFilter] = useState<Filter>("all");
  if (!roadmap) return <section className="roadmap-page"><p className="quiet">Loading…</p></section>;

  const count = (status: string) => roadmap.milestones.filter((m) => m.status === status).length;
  const planned = roadmap.milestones.length - count("later"); // ideas without a number aren't part of the progress
  const shown = roadmap.milestones.filter((m) => filter === "all" || m.status === filter);

  return (
    <section className="roadmap-page">
      <h2>Roadmap</h2>
      <div className="roadmap-summary">
        <div
          className="roadmap-meter"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={planned}
          aria-valuenow={count("done")}
          aria-label="Milestones done"
        >
          <span style={{ width: planned ? `${(100 * count("done")) / planned}%` : 0 }} />
        </div>
        <p>
          <b>{count("done")}</b> of {planned} milestones done · <b>{count("open")}</b> open · <b>{count("later")}</b> later
        </p>
        <div className="roadmap-filters" role="group" aria-label="Show">
          {(Object.keys(FILTERS) as Filter[]).map((name) => (
            <button key={name} aria-pressed={filter === name} onClick={() => setFilter(name)}>
              {FILTERS[name]}
            </button>
          ))}
        </div>
      </div>
      {roadmap.notes.length > 0 && (
        <ul className="roadmap-notes">
          {roadmap.notes.map((note, i) => (
            <li key={i} className="error">
              {note}
            </li>
          ))}
        </ul>
      )}
      <ol className="roadmap-list">
        {shown.map((m, i) => (
          <Milestone key={`${m.id}-${i}`} m={m} />
        ))}
      </ol>
      <p className="quiet">Read from docs/roadmap.md: edit the document, not this page.</p>
    </section>
  );
}

function Milestone({ m }: { m: RoadmapMilestone }) {
  // Groups in document order, each item under the bold line it followed.
  const groups = m.items.reduce<{ name: string; items: string[] }[]>((all, item) => {
    const last = all[all.length - 1];
    if (last && last.name === item.group) last.items.push(item.text);
    else all.push({ name: item.group, items: [item.text] });
    return all;
  }, []);
  return (
    <li className={`milestone ${m.status}`}>
      <div className="head">
        <span className="id">{m.id}</span>
        <span className={`status ${m.status}`}>{m.status === "done" ? "Done" : m.status === "later" ? "Later" : "Open"}</span>
      </div>
      <h3>{m.name}</h3>
      {m.can && (
        <p>
          <span className="quiet">You can </span>
          <Inline text={m.can} />
        </p>
      )}
      {m.proves && (
        <p>
          <span className="quiet">Proves </span>
          <Inline text={m.proves} />
        </p>
      )}
      {m.items.length > 0 && (
        <details open={m.status === "open"}>
          <summary>
            {m.items.length} {m.items.length === 1 ? "item" : "items"}
          </summary>
          {groups.map((g, i) => (
            <div key={i}>
              {g.name && <h4>{g.name}</h4>}
              <ul>
                {g.items.map((text, j) => (
                  <li key={j}>
                    <Inline text={text} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      )}
    </li>
  );
}

// **bold**, `code` and [text](address): all the document uses inline. Anything else shows as written.
const INLINE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;

function Inline({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const found of text.matchAll(INLINE)) {
    parts.push(text.slice(at, found.index));
    const [whole, bold, code, label, href] = found;
    if (bold) parts.push(<strong key={found.index}>{bold}</strong>);
    else if (code) parts.push(<code key={found.index}>{code}</code>);
    // A relative link points into the repo's docs, which the panel doesn't serve: plain words.
    else if (/^https?:\/\//.test(href)) {
      parts.push(
        <a key={found.index} href={href} target="_blank" rel="noreferrer" className="linked">
          {label}
        </a>,
      );
    } else parts.push(label);
    at = found.index + whole.length;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}
