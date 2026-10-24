// Live activity inside Archangel (apps/agent/src/agent/activity.py), streamed from /api/activity.

import { useEffect, useState } from "react";

export type Notice = {
  id: number;
  time: number;
  source: string;
  target: string;
  /** tx: a request or message going out; rx: the reply coming back. */
  dir: "tx" | "rx";
  /** agent, model, phone, service (a connector) and twin (a page of the context twin) are always calls (a tx, then
   * an rx); the rest are one-way. */
  kind: "message" | "agent" | "model" | "tool" | "phone" | "service" | "twin" | "task";
  text: string;
  request: number | null;
  task: number | null;
  ok: boolean;
  /** What each end is, as declared by the agent: names are never hard-coded here. */
  source_role: Role;
  target_role: Role;
};
export type Role = "channel" | "agent" | "model" | "device" | "service" | "twin" | "part";

const KEPT = 400;
const RETRY_MS = 2000;
/** Kinds that are always calls: a tx from the caller, then an rx back. */
export const CALLS = new Set(["agent", "model", "phone", "service", "twin"]);
const CALL_GIVEN_UP_SECONDS = 120; // a reply lost in the stream mustn't leave a node busy forever

/** The latest notices, oldest first. On reconnecting, the agent replays its recent ones. */
export function useActivity(): Notice[] {
  const [notices, setNotices] = useState<Notice[]>([]);
  useEffect(() => {
    let stream: EventSource;
    let retry: number;
    let frame = 0;
    const pending: Notice[] = [];
    const connect = () => {
      stream = new EventSource("/api/activity");
      stream.onopen = () => setNotices([]);
      stream.onmessage = (e) => {
        // Notices come in bursts; one render per frame, however many arrived.
        pending.push(JSON.parse(e.data));
        frame ||= requestAnimationFrame(() => {
          const fresh = pending.splice(0);
          frame = 0;
          setNotices((old) => [...old, ...fresh].slice(-KEPT));
        });
      };
      // The browser retries a dropped stream by itself, but gives up for good on an error response, which is
      // what the dev proxy sends while the agent is down.
      stream.onerror = () => {
        if (stream.readyState === EventSource.CLOSED) retry = setTimeout(connect, RETRY_MS);
      };
    };
    connect();
    return () => {
      clearTimeout(retry);
      cancelAnimationFrame(frame);
      stream.close();
    };
  }, []);
  return notices;
}

/** The calls still waiting on a reply, by the node working on them. */
export function openCalls(notices: Notice[], now: number): Map<string, Notice> {
  const last = new Map<string, Notice>(); // by caller and callee
  for (const n of notices) {
    if (!CALLS.has(n.kind)) continue;
    const [caller, callee] = n.dir === "tx" ? [n.source, n.target] : [n.target, n.source];
    last.set(`${caller}>${callee}`, n);
  }
  const open = new Map<string, Notice>();
  for (const n of last.values()) {
    if (n.dir === "tx" && now - n.time < CALL_GIVEN_UP_SECONDS) open.set(n.target, n);
  }
  return open;
}
