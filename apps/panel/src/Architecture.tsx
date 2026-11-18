import dagre from "@dagrejs/dagre";
import {
  Background,
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  Handle,
  type Node,
  type NodeProps,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { type Diagram, FLOW, type Part, SCENARIOS } from "./blueprint";
import { Redrawn } from "./Cockpit";

const MODES = {
  flow: "How an event moves through Archangel, with every decision on the way. Click a part for what it does.",
  trace: "Real things the owner says, played through the flow a step at a time.",
};
type Mode = keyof typeof MODES;

/** How Archangel works: the flow of an event, each decision on the way, and scenarios played through it. Drawn from
 * blueprint.ts, written by hand. */
export function Architecture() {
  const [mode, setMode] = useState<Mode>("flow");
  const [selected, select] = useState<string>();
  const [scenario, setScenario] = useState(0);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  const steps = SCENARIOS[scenario].steps;
  useEffect(() => {
    if (!playing) return;
    if (step >= steps.length - 1) return setPlaying(false);
    const timer = setTimeout(() => setStep((s) => s + 1), 1300);
    return () => clearTimeout(timer);
  }, [playing, step, steps.length]);

  const walked = steps.slice(0, step + 1).map((s) => s.at);
  const trace = mode === "trace" ? { visited: new Set(walked), path: walked, here: walked.at(-1)! } : undefined;

  return (
    <>
      <section className="architecture-graph">
        <Redrawn>
          <ReactFlowProvider>
            <Lit.Provider value={{ selected, trace }}>
              <Drawing select={(id) => (select(id), setMode("flow"), setPlaying(false))} follow={trace?.here}>
                <Panel position="top-left" className="architecture-modes">
                  {(Object.keys(MODES) as Mode[]).map((each) => (
                    <button key={each} className={mode === each ? "on" : ""} onClick={() => (setMode(each), setPlaying(false))}>
                      {each}
                    </button>
                  ))}
                  <p>
                    <span className="quiet">{MODES[mode]}</span>
                  </p>
                </Panel>
                <Panel position="bottom-left" className="architecture-key">
                  {KEY.map(([kind, label]) => (
                    <span key={kind} className={`swatch ${kind}`}>{label}</span>
                  ))}
                </Panel>
              </Drawing>
            </Lit.Provider>
          </ReactFlowProvider>
        </Redrawn>
      </section>
      <section className="architecture-side">
        {mode === "trace" ? (
          <Trace
            scenario={scenario}
            choose={(i) => (setScenario(i), setStep(0), setPlaying(false))}
            step={step}
            setStep={(s) => (setStep(s), setPlaying(false))}
            playing={playing}
            play={() => (step >= steps.length - 1 && setStep(0), setPlaying(!playing))}
          />
        ) : (
          <About id={selected} select={select} />
        )}
      </section>
    </>
  );
}

const KEY = [
  ["channel", "channel"],
  ["agent", "agent"],
  ["model", "model call"],
  ["twin", "twin"],
  ["service", "service"],
  ["device", "device"],
  ["owner", "the owner"],
] as const;

type Trace = { visited: Set<string>; path: string[]; here: string };
/** What's lit, kept out of the nodes themselves: React Flow loses a node's measurements when it's replaced. */
const Lit = createContext<{ selected?: string; trace?: Trace }>({});

type Box = { width: number; height: number };
type PartData = Part;
type WireData = { points: { x: number; y: number }[]; label?: string; spot?: { x: number; y: number }; back?: boolean };

const nodeTypes = { part: PartNode };
const edgeTypes = { wire: WireEdge };

/** In a trace, follows the step it's at; otherwise shows the whole flow. */
function Drawing(props: { select: (id: string) => void; follow?: string; children: React.ReactNode }) {
  const { select, follow } = props;
  const { nodes, edges } = useMemo(() => laidOut(FLOW), []);
  const flow = useReactFlow();
  useEffect(() => {
    const node = follow && nodes.find((n) => n.id === follow);
    if (node) flow.setCenter(node.position.x + node.width! / 2, node.position.y + node.height! / 2, { zoom: 1, duration: 500 });
    else flow.fitView({ padding: 0.08, duration: 500 });
  }, [follow]);
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      colorMode="dark"
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      onNodeClick={(_, node) => select(node.id)}
      fitView
      fitViewOptions={{ padding: 0.08 }}
      minZoom={0.15}
    >
      <Background gap={28} size={1} />
      {props.children}
    </ReactFlow>
  );
}

/** Laid out once by dagre, wires included: they follow dagre's routes, so they bend around the parts. */
function laidOut({ parts, wires }: Diagram): { nodes: Node<PartData>[]; edges: Edge<WireData>[] } {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: 28, ranksep: 46, edgesep: 14 });
  const sizes = new Map(parts.map((p) => [p.id, size(p)]));
  for (const p of parts) graph.setNode(p.id, { ...sizes.get(p.id)! });
  // Loops back (a task's update waking the orchestrator) are left out of the layout, or the starts sink to the bottom.
  for (const w of wires.filter((w) => !w.back)) {
    graph.setEdge(w.from, w.to, w.label ? { label: w.label, width: w.label.length * 6 + 10, height: 14, labelpos: "c" } : {});
  }
  dagre.layout(graph);
  const nodes = parts.map((p) => {
    const { x, y } = graph.node(p.id);
    const { width, height } = sizes.get(p.id)!;
    return { id: p.id, type: "part", position: { x: x - width / 2, y: y - height / 2 }, width, height, data: p };
  });
  const edges = wires.map((w) => {
    const route = w.back ? backRoute(graph.node(w.from), graph.node(w.to)) : graph.edge(w.from, w.to);
    return {
      id: `${w.from}>${w.to}`,
      source: w.from,
      target: w.to,
      type: "wire",
      data: { points: route.points, label: w.label, spot: w.label ? { x: route.x, y: route.y } : undefined, back: w.back },
    };
  });
  return { nodes, edges };
}

type Spot = { x: number; y: number; width: number; height: number };

/** A loop back, drawn on top of the layout: from the side of one part to the side of the other. */
function backRoute(from: Spot, to: Spot) {
  const side = (spot: Spot, toward: Spot) => ({ x: spot.x + (toward.x > spot.x ? 1 : -1) * (spot.width / 2), y: spot.y });
  const [a, b] = [side(from, to), side(to, from)];
  return { points: [a, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, b], x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

const CHAR = 6.7; // JetBrains Mono at 11px

function size(part: Part): Box {
  const width = part.shape === "decision" ? 200 : 180;
  const room = width - (part.shape === "decision" ? 44 : 20);
  const lines = Math.ceil((part.label.length * CHAR) / (room - 2 * CHAR));
  return { width, height: Math.max(part.shape === "decision" ? 48 : 34, lines * 15 + (part.shape === "decision" ? 22 : 16)) };
}

function PartNode({ id, data }: NodeProps<Node<PartData>>) {
  const { selected, trace } = useContext(Lit);
  const classes = [
    "arch-part",
    data.kind,
    data.shape ?? "box",
    selected === id && "selected",
    trace?.visited.has(id) && "visited",
    trace?.here === id && "here",
    trace && !trace.visited.has(id) && "dim",
  ];
  return (
    <div className={classes.filter(Boolean).join(" ")}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
      <span>{data.label}</span>
    </div>
  );
}

function WireEdge({ id, source, target, data }: EdgeProps<Edge<WireData>>) {
  const { selected, trace } = useContext(Lit);
  const walked = trace?.path.some((at, i) => at === source && trace.path[i + 1] === target);
  const touching = selected === source || selected === target;
  const classes = ["react-flow__edge-path", data!.back && "back", (walked || touching) && "lit", trace && !walked && "dim"];
  return (
    <>
      <path id={id} className={classes.filter(Boolean).join(" ")} d={smooth(data!.points)} fill="none" markerEnd="url(#arch-arrow)" />
      {data!.label && data!.spot && (
        <EdgeLabelRenderer>
          <div
            className={["wire-label", (walked || touching) && "lit", trace && !walked && "dim"].filter(Boolean).join(" ")}
            style={{ transform: `translate(-50%, -50%) translate(${data!.spot.x}px, ${data!.spot.y}px)` }}
          >
            {data!.label}
          </div>
        </EdgeLabelRenderer>
      )}
      <Arrow />
    </>
  );
}

/** One arrowhead definition, shared; defined again by each wire, which the browser tolerates. */
function Arrow() {
  return (
    <defs>
      <marker id="arch-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L8,4 L0,8 z" className="arch-arrow" />
      </marker>
    </defs>
  );
}

/** Through dagre's points, rounding each bend. */
function smooth(points: { x: number; y: number }[]): string {
  if (points.length < 3) return `M${points[0].x},${points[0].y} L${points[points.length - 1].x},${points[points.length - 1].y}`;
  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const mid = { x: (points[i].x + points[i + 1].x) / 2, y: (points[i].y + points[i + 1].y) / 2 };
    d += ` Q${points[i].x},${points[i].y} ${i === points.length - 2 ? `${points[i + 1].x},${points[i + 1].y}` : `${mid.x},${mid.y}`}`;
  }
  return d;
}

function About({ id, select }: { id?: string; select: (id: string) => void }) {
  const diagram = FLOW;
  const part = diagram.parts.find((p) => p.id === id);
  if (!part) {
    return (
      <>
        <h2>Architecture</h2>
        <p className="quiet">Click a part to see what it does, where it lives, and what it connects to.</p>
      </>
    );
  }
  const name = (pid: string) => diagram.parts.find((p) => p.id === pid)!.label;
  const ins = diagram.wires.filter((w) => w.to === part.id);
  const outs = diagram.wires.filter((w) => w.from === part.id);
  return (
    <div className="architecture-about">
      <h2>{part.label}</h2>
      <p>
        <span className={`swatch ${part.kind}`}>{part.shape === "decision" ? "decision" : part.kind}</span>
        {part.file && <code>{part.file}</code>}
      </p>
      <p>{part.what}</p>
      <Ends title="From" ends={ins.map((w) => [w.from, w.label])} name={name} select={select} />
      <Ends title="To" ends={outs.map((w) => [w.to, w.label])} name={name} select={select} />
    </div>
  );
}

function Ends(props: { title: string; ends: [string, string | undefined][]; name: (id: string) => string; select: (id: string) => void }) {
  if (!props.ends.length) return null;
  return (
    <>
      <h3>{props.title}</h3>
      <ul>
        {props.ends.map(([id, label]) => (
          <li key={id}>
            <a onClick={() => props.select(id)}>{props.name(id)}</a>
            {label && <span className="quiet"> · {label}</span>}
          </li>
        ))}
      </ul>
    </>
  );
}

function Trace(props: {
  scenario: number;
  choose: (i: number) => void;
  step: number;
  setStep: (s: number) => void;
  playing: boolean;
  play: () => void;
}) {
  const { scenario, choose, step, setStep, playing, play } = props;
  const { steps } = SCENARIOS[scenario];
  useEffect(() => {
    document.querySelector(".trace-steps .now")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [step]);
  return (
    <div className="architecture-trace">
      <h2>Trace</h2>
      <ul className="trace-scenarios">
        {SCENARIOS.map((s, i) => (
          <li key={s.title}>
            <button className={i === scenario ? "on" : ""} onClick={() => choose(i)}>
              {s.title}
            </button>
          </li>
        ))}
      </ul>
      <div className="actions">
        <button disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        <button className="approve" onClick={play}>{playing ? "Pause" : step >= steps.length - 1 ? "Replay" : "Play"}</button>
        <button disabled={step >= steps.length - 1} onClick={() => setStep(step + 1)}>Next</button>
        <span className="quiet">{step + 1}/{steps.length}</span>
      </div>
      <ol className="trace-steps">
        {steps.map((s, i) => (
          <li key={i} className={i === step ? "now" : i < step ? "past" : "later"} onClick={() => setStep(i)}>
            <span className="quiet">{FLOW.parts.find((p) => p.id === s.at)!.label}</span>
            <p>{s.say}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
