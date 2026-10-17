// Live activity inside Archangel (apps/agent/src/agent/activity.py), streamed from /api/activity.

import { useEffect, useState } from "react";

export type Notice = {
  id: number;
  time: number;
  source: string;
  target: string;
  /** tx: a request or message going out; rx: the reply coming back. */
  dir: "tx" | "rx";
  /** agent, model and phone are always calls (a tx, then an rx); the rest are one-way. */
  kind: "message" | "agent" | "model" | "tool" | "phone" | "task";
  text: string;
  request: number | null;
  task: number | null;
  ok: boolean;
};

const KEPT = 400;
const RETRY_MS = 2000;
const CALLS = new Set(["agent", "model", "phone"]);
const CALL_GIVEN_UP_SECONDS = 120; // a reply lost in the stream mustn't leave a node busy forever

/** The latest notices, oldest first. On reconnecting, the agent replays its recent ones. */
export function useActivity(): Notice[] {
  const [notices, setNotices] = useState<Notice[]>([]);
  useEffect(() => {
    let stream: EventSource;
    let retry: number;
    const connect = () => {
      stream = new EventSource("/api/activity");
      stream.onopen = () => setNotices([]);
      stream.onmessage = (e) => {
        const notice: Notice = JSON.parse(e.data);
        setNotices((old) => [...old.slice(1 - KEPT), notice]);
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
