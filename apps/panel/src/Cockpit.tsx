import { type CSSProperties, useEffect, useState } from "react";
import { type Notice, openCalls, useActivity } from "./activity";

// Fixed places for the parts of Archangel; channels and models are placed as they turn up.
const PLACES: Record<string, [number, number]> = {
  gateway: [230, 300],
  orchestrator: [400, 300],
  tasks: [560, 300],
  pilot: [720, 300],
  phone: [900, 300],
  guard: [600, 470],
  locator: [760, 470],
  investigator: [910, 470],
};
const AGENTS = new Set(["orchestrator", "pilot", "guard", "locator", "investigator"]);
const MODEL_GAP = 170;
const PULSE_SECONDS = 1.2;
const TICK_MS = 250;
const SHOWN = 150;

type Follow = number | "live" | null;

export function Cockpit() {
  const notices = useActivity();
  const now = useNow();
  const [follow, setFollow] = useState<Follow>("live");
  const request = follow === "live" ? (notices.findLast((n) => n.request !== null)?.request ?? null) : follow;
  const inRequest = (n: Notice) => request === null || n.request === request;

  return (
    <>
      <section className="cockpit">
        <Graph notices={notices} now={now} inRequest={inRequest} following={request !== null} />
      </section>
      <section className="timeline">
        <div className="follow">
          <h2>Activity</h2>
          <button className={follow === "live" ? "active" : ""} onClick={() => setFollow("live")}>
            Follow latest
          </button>
          <button className={follow === null ? "active" : ""} onClick={() => setFollow(null)}>
            Everything
          </button>
        </div>
        {notices.length === 0 && <p className="quiet">Quiet. Say something to Archangel and watch it travel.</p>}
        <ol>
          {notices
            .slice(-SHOWN)
            .reverse()
            .map((n) => (
              <li
                key={n.id}
                className={`${n.dir} ${n.ok ? "" : "failed"} ${inRequest(n) ? "" : "dim"}`}
                onClick={() => n.request !== null && setFollow(n.request === follow ? "live" : n.request)}
              >
                <time>{new Date(n.time * 1000).toLocaleTimeString([], { hour12: false })}</time>
                <span className="hop">
                  {n.source} → {n.target} <span className="dir">{n.dir}</span>
                </span>
                {n.request !== null && <span className="request">#{n.request}</span>}
                <span className="text">{n.text}</span>
              </li>
            ))}
        </ol>
      </section>
    </>
  );
}

function Graph({
  notices,
  now,
  inRequest,
  following,
}: {
  notices: Notice[];
  now: number;
  inRequest: (n: Notice) => boolean;
  following: boolean;
}) {
  const places = layout(notices);
  const open = openCalls(notices, now);
  const followed = notices.filter(inRequest);
  const here = following ? followed[followed.length - 1]?.target : undefined;
  const path = new Set(following ? followed.map((n) => edge(n.source, n.target)) : []);
  const edges = new Set(notices.filter((n) => n.source !== n.target).map((n) => edge(n.source, n.target)));
  const fresh = notices.filter((n) => now - n.time < PULSE_SECONDS);
  const flashing = new Set(fresh.filter((n) => n.source === n.target).map((n) => n.source));

  return (
    <svg viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet">
      {[...edges].map((key) => {
        const [a, b] = key.split("|").map((name) => places.get(name)!);
        return (
          <line key={key} className={`edge ${path.has(key) ? "path" : ""}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />
        );
      })}
      {fresh
        .filter((n) => n.source !== n.target)
        .map((n) => {
          const [x1, y1] = places.get(n.source)!;
          const [x2, y2] = places.get(n.target)!;
          // Requests and replies travel either side of the line, so a call and its answer don't collide.
          const length = Math.hypot(x2 - x1, y2 - y1) || 1;
          const side = n.dir === "tx" ? -5 : 5;
          const [ox, oy] = [(-(y2 - y1) / length) * side, ((x2 - x1) / length) * side];
          const style = { "--dx": `${x2 - x1}px`, "--dy": `${y2 - y1}px` } as CSSProperties;
          return (
            <g key={n.id} transform={`translate(${x1 + ox} ${y1 + oy})`}>
              <circle
                className={`pulse ${n.dir} ${n.ok ? "" : "failed"} ${inRequest(n) ? "" : "dim"}`}
                r={6}
                style={style}
              />
            </g>
          );
        })}
      {[...places].map(([name, [x, y]]) => {
        const call = open.get(name);
        const classes = [
          "node",
          kindOf(name, notices),
          call && "busy",
          name === here && "here",
          flashing.has(name) && "flash",
        ];
        return (
          <g key={name} className={classes.filter(Boolean).join(" ")} transform={`translate(${x} ${y})`}>
            <rect x={-width(name) / 2} y={-18} width={width(name)} height={36} rx={18} />
            <text dy={4}>{name}</text>
            {call && (
              <text className="doing" dy={36}>
                {clip(call.text, 28)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** Where every node seen so far goes: the fixed parts, channels down the left, models along the top. */
function layout(notices: Notice[]): Map<string, [number, number]> {
  const channels: string[] = [];
  const others: string[] = [];
  for (const n of notices) {
    for (const name of [n.source, n.target]) {
      if (name in PLACES || channels.includes(name) || others.includes(name)) continue;
      (kindOf(name, notices) === "channel" ? channels : others).push(name);
    }
  }
  const places = new Map<string, [number, number]>(Object.entries(PLACES));
  channels.forEach((name, i) => places.set(name, [70, 300 + (i - (channels.length - 1) / 2) * 90]));
  // Each model sits above the parts that call it, spread out so none overlap.
  const callers = (name: string) =>
    notices.flatMap((n) => (n.target === name ? [n.source] : n.source === name ? [n.target] : []));
  const xs = others.map((name) => {
    const near = callers(name).flatMap((c) => (places.has(c) ? [places.get(c)![0]] : []));
    return [name, near.length ? near.reduce((a, b) => a + b) / near.length : 500] as const;
  });
  let previous = -Infinity;
  for (const [name, x] of xs.sort((a, b) => a[1] - b[1])) {
    previous = Math.max(x, previous + MODEL_GAP);
    places.set(name, [previous, 120]);
  }
  return places;
}

function kindOf(name: string, notices: Notice[]): string {
  if (AGENTS.has(name)) return "agent";
  if (name in PLACES) return name === "phone" ? "device" : "part";
  const met = notices.find((n) => n.source === name || n.target === name);
  return met?.kind === "message" ? "channel" : "model";
}

const width = (name: string) => name.length * 7.5 + 32;
const edge = (a: string, b: string) => [a, b].sort().join("|");
const clip = (text: string, length: number) => (text.length > length ? `${text.slice(0, length)}…` : text);

/** The time now, in seconds, updated a few times a second so pulses come and go. */
function useNow(): number {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}
