import dagre from "@dagrejs/dagre";
import {
  Background,
  BaseEdge,
  type Edge,
  type EdgeProps,
  getBezierPath,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Component, type CSSProperties, Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { CALLS, type Notice, openCalls, type Role, useActivity } from "./activity";
import { useNarrow } from "./narrow";
import { play, throttled } from "./sounds";

const NODE = { width: 120, height: 44 }; // as in styles.css
const PULSE_SECONDS = 1.2;
const TICK_MS = 400;
const SHOWN = 150;

type Follow = number | "live" | null;

export function Cockpit() {
  const notices = useActivity();
  useTrafficSounds(notices);
  const now = useNow();
  const [follow, setFollow] = useState<Follow>("live");
  const [opened, setOpened] = useState<number | null>(null);
  const request = follow === "live" ? (notices.findLast((n) => n.request !== null)?.request ?? null) : follow;
  const inRequest = (n: Notice) => request === null || n.request === request;

  return (
    <>
      <section className="cockpit">
        <Redrawn>
          <ReactFlowProvider>
            <Graph notices={notices} now={now} inRequest={inRequest} following={request !== null} />
          </ReactFlowProvider>
        </Redrawn>
      </section>
      <section className="timeline">
        <div className="follow">
          <h2>Activity</h2>
          <button className={follow === "live" ? "active" : ""} onClick={() => setFollow("live")}>
            Latest
          </button>
          <button className={follow === null ? "active" : ""} onClick={() => setFollow(null)}>
            All
          </button>
        </div>
        {notices.length === 0 && <p className="quiet">Quiet. Say something to Archangel and watch it travel.</p>}
        <ol>
          {notices
            .slice(-SHOWN)
            .reverse()
            .map((n) => (
              <Fragment key={n.id}>
                <li
                  title={n.text}
                  className={`${n.dir} ${n.ok ? "" : "failed"} ${inRequest(n) ? "" : "dim"} ${opened === n.id ? "open" : ""}`}
                  onClick={() => {
                    // Opening a notice also follows its request; closing it goes back to the latest.
                    const closing = opened === n.id;
                    setOpened(closing ? null : n.id);
                    setFollow(closing || n.request === null ? "live" : n.request);
                  }}
                >
                  <time>{new Date(n.time * 1000).toLocaleTimeString([], { hour12: false })}</time>
                  <span className="hop">
                    {n.source} → {n.target}
                  </span>
                  <span className="text">{n.text}</span>
                  <span className="request">{n.request !== null && `#${n.request}`}</span>
                </li>
                {opened === n.id && (
                  <li className="detail">
                    <p>
                      {n.kind} {n.dir === "tx" ? "sent" : "reply"}
                      {n.task !== null && ` · task #${n.task}`}
                      {!n.ok && " · failed"}
                    </p>
                    {n.text || <span className="quiet">(nothing said)</span>}
                  </li>
                )}
              </Fragment>
            ))}
        </ol>
      </section>
    </>
  );
}

/** If drawing the graph fails, says why and offers to try again, instead of the graph silently vanishing. */
export class Redrawn extends Component<{ children: ReactNode }, { error: Error | null; tries: number }> {
  state = { error: null as Error | null, tries: 0 };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("Cockpit graph failed", error);
  }
  render() {
    const { error, tries } = this.state;
    if (!error) return <Fragment key={tries}>{this.props.children}</Fragment>;
    return (
      <p className="quiet">
        The graph hit a problem: {error.message}{" "}
        <button onClick={() => this.setState({ error: null, tries: tries + 1 })}>Redraw</button>
      </p>
    );
  }
}

type PartData = { name: string; kind: string; doing?: string; here: boolean; flash: boolean; down: boolean };
type Pulse = { id: number; back: boolean; dir: Notice["dir"]; ok: boolean; dim: boolean };
type HopData = { lit: boolean; pulses: Pulse[] };

const nodeTypes = { part: Part };
const edgeTypes = { hop: Hop };

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
  const kindOf = roles(notices);
  const { names, links } = useTopology(notices, kindOf);
  // A phone held upright has room down, not across.
  const down = useNarrow();
  const shape = names.join() + "|" + links.map((l) => l.join(">")).join() + (down ? "|down" : "");
  // Laid out again only when a part or a link first appears, so nothing moves while you watch.
  const spots = useMemo(() => layout(names, links, down), [shape]);

  const open = openCalls(notices, now);
  const followed = notices.filter(inRequest);
  const here = following ? followed[followed.length - 1]?.target : undefined;
  const lit = new Set(following ? followed.map((n) => pair(n.source, n.target)) : []);
  const fresh = notices.filter((n) => now - n.time < PULSE_SECONDS);

  const nodes: Node<PartData>[] = names.map((name) => ({
    id: name,
    type: "part",
    position: spots.get(name)!,
    data: {
      name,
      kind: kindOf.get(name) ?? "part",
      doing: open.get(name)?.text,
      here: name === here,
      flash: fresh.some((n) => n.source === name && n.target === name),
      down,
    },
  }));
  const edges: Edge<HopData>[] = links.map(([from, to]) => {
    const key = pair(from, to);
    const pulses = fresh
      .filter((n) => n.source !== n.target && pair(n.source, n.target) === key)
      .map((n) => ({ id: n.id, back: n.source !== from, dir: n.dir, ok: n.ok, dim: !inRequest(n) }));
    return { id: key, source: from, target: to, type: "hop", data: { lit: lit.has(key), pulses } };
  });

  const flow = useReactFlow();
  // Fit to the layout's own bounds, not React Flow's measurements: parts that have just appeared aren't measured
  // yet, and fitting without them zoomed in on the old ones, leaving the rest off-screen (the cockpit "vanishing").
  useEffect(() => {
    if (spots.size) flow.fitBounds(bounds(spots), { padding: 0.06, duration: 400 });
  }, [shape]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      nodeOrigin={[0.5, 0.5]}
      colorMode="dark"
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      fitView
      minZoom={0.3}
    >
      <Background gap={28} size={1} />
    </ReactFlow>
  );
}

function Part({ data }: NodeProps<Node<PartData>>) {
  const classes = ["part", data.kind, data.doing && "busy", data.here && "here", data.flash && "flash"];
  return (
    <div className={classes.filter(Boolean).join(" ")}>
      <Handle type="target" position={data.down ? Position.Top : Position.Left} isConnectable={false} />
      <Handle type="source" position={data.down ? Position.Bottom : Position.Right} isConnectable={false} />
      <span className="name">{data.name}</span>
      <span className="doing">{data.doing ?? " "}</span>
    </div>
  );
}

function Hop({ id, data, ...ends }: EdgeProps<Edge<HopData>>) {
  const [path] = getBezierPath(ends);
  return (
    <>
      <BaseEdge id={id} path={path} className={data?.lit ? "lit" : ""} />
      {data?.pulses.map((p) => (
        <circle
          key={p.id}
          r={5}
          className={["pulse", p.dir, p.back && "back", !p.ok && "failed", p.dim && "dim"].filter(Boolean).join(" ")}
          style={{ offsetPath: `path("${path}")` } as CSSProperties}
        />
      ))}
    </>
  );
}

/** Every part and link seen since the cockpit opened, each link pointing from caller to callee. Calls say
 * who's calling; channels always feed the gateway; for other messages, the first one in a request we saw begin
 * (a page opened mid-request sees replies before what they answer). */
function useTopology(notices: Notice[], kindOf: Map<string, Role>): { names: string[]; links: [string, string][] } {
  const seen = useRef({
    names: new Set<string>(),
    links: new Map<string, [string, string]>(),
    whole: new Map<number, boolean>(), // requests, by whether we saw them begin
  }).current;
  for (const n of notices) {
    seen.names.add(n.source).add(n.target);
    if (n.request !== null && !seen.whole.has(n.request)) seen.whole.set(n.request, kindOf.get(n.source) === "channel");
    if (n.source === n.target || n.dir !== "tx") continue;
    const key = pair(n.source, n.target);
    if (kindOf.get(n.target) === "channel") seen.links.set(key, [n.target, n.source]);
    else if (CALLS.has(n.kind) || kindOf.get(n.source) === "channel") seen.links.set(key, [n.source, n.target]);
    else if (!seen.links.has(key) && n.request !== null && seen.whole.get(n.request)) {
      seen.links.set(key, [n.source, n.target]);
    }
  }
  // A link with no direction yet still belongs in the picture.
  for (const n of notices) {
    const key = pair(n.source, n.target);
    if (n.source !== n.target && !seen.links.has(key)) seen.links.set(key, [n.source, n.target]);
  }
  return { names: [...seen.names].sort(), links: [...seen.links.values()] };
}

/** Left to right (or top to bottom, `down`) in the order things call each other: channels, gateway, orchestrator,
 * tasks, pilot, and each model or helper beside whatever calls it. */
function layout(names: string[], links: [string, string][], down: boolean): Map<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph(down ? { rankdir: "TB", nodesep: 12, ranksep: 28 } : { rankdir: "LR", nodesep: 18, ranksep: 44 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const name of names) graph.setNode(name, { ...NODE });
  for (const [from, to] of links) graph.setEdge(from, to);
  dagre.layout(graph);
  return new Map(names.map((name) => [name, { x: graph.node(name).x, y: graph.node(name).y }]));
}

/** The box around every part, as laid out (positions are centres). */
function bounds(spots: Map<string, { x: number; y: number }>) {
  const xs = [...spots.values()].map((p) => p.x);
  const ys = [...spots.values()].map((p) => p.y);
  const [left, top] = [Math.min(...xs) - NODE.width / 2, Math.min(...ys) - NODE.height / 2];
  return { x: left, y: top, width: Math.max(...xs) + NODE.width / 2 - left, height: Math.max(...ys) + NODE.height / 2 - top };
}

/** Each node's role, as the agent declared it; the latest word wins, as a part's role becomes known. */
function roles(notices: Notice[]): Map<string, Role> {
  const roles = new Map<string, Role>();
  for (const n of notices) {
    if (n.source_role !== "part" || !roles.has(n.source)) roles.set(n.source, n.source_role);
    if (n.target_role !== "part" || !roles.has(n.target)) roles.set(n.target, n.target_role);
  }
  return roles;
}

const pair = (a: string, b: string) => [a, b].sort().join("|");

/** The time now, in seconds, updated a few times a second so pulses and busy marks come and go. */
function useNow(): number {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** A tick for each request out (tx) and reply back (rx) as it happens; not for what's replayed on connecting. */
function useTrafficSounds(notices: Notice[]) {
  const heard = useRef(0);
  const sounds = useMemo(() => ({ tx: throttled(60, () => play("tx")), rx: throttled(60, () => play("rx")) }), []);
  useEffect(() => {
    const fresh = notices.filter((n) => n.id > heard.current);
    if (!fresh.length) return;
    heard.current = fresh[fresh.length - 1].id;
    for (const n of fresh) if (Date.now() / 1000 - n.time < 3) sounds[n.dir]();
  }, [notices, sounds]);
}
