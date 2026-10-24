import {
  Background,
  BaseEdge,
  type Edge,
  type EdgeProps,
  Handle,
  type InternalNode,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useInternalNode,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { useEffect, useMemo, useState } from "react";
import { api, type TwinGraph, type TwinNode } from "./api";
import { Redrawn } from "./Cockpit";
import { usePoll } from "./poll";

const ROOT = "You";
const NODE = { width: 132, height: 40 }; // as in styles.css

/** The context twin: the owner's second brain, a graph of pages the agents read before acting (docs/twin.md). */
export function Twin() {
  const graph = usePoll(api.twin);
  const [selected, setSelected] = useState(ROOT);
  const known = !graph || graph.nodes.some((n) => n.name === selected);
  const name = known ? selected : ROOT; // deleted, or merged away: back to the root

  return (
    <>
      <section className="twin-graph">
        {graph && (
          <Redrawn>
            <ReactFlowProvider>
              <Brain graph={graph} selected={name} select={setSelected} />
            </ReactFlowProvider>
          </Redrawn>
        )}
      </section>
      <section className="twin-node">
        <Page name={name} graph={graph} select={setSelected} />
      </section>
    </>
  );
}

type ThoughtData = { name: string; router: boolean; links: number; selected: boolean };
type TieData = { lit: boolean; stale: boolean };

const nodeTypes = { thought: Thought };
const edgeTypes = { tie: Tie };

function Brain({ graph, selected, select }: { graph: TwinGraph; selected: string; select: (name: string) => void }) {
  const shape = graph.nodes.map((n) => n.name).join() + "|" + graph.links.map((l) => `${l.source}>${l.target}`).join();
  // Laid out again only when a node or link appears or goes, so nothing moves while you read.
  const spots = useMemo(() => layout(graph), [shape]);
  const degree = (name: string) => graph.links.filter((l) => l.source === name || l.target === name).length;

  // React Flow's own state, so each node keeps its measured size: a fresh node object without one is hidden until
  // measured again, and an unchanged box never is.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<ThoughtData>>([]);
  const routers = graph.nodes.filter((n) => n.router).map((n) => n.name).join();
  useEffect(() => {
    setNodes((old) => {
      const kept = new Map(old.map((n) => [n.id, n]));
      return graph.nodes.map((n) => ({
        ...kept.get(n.name),
        id: n.name,
        type: "thought",
        position: spots.get(n.name) ?? { x: 0, y: 0 },
        data: { name: n.name, router: n.router, links: degree(n.name), selected: n.name === selected },
      }));
    });
  }, [shape, routers, selected]);
  const edges: Edge<TieData>[] = graph.links.map((l) => ({
    id: `${l.source}>${l.target}`,
    source: l.source,
    target: l.target,
    type: "tie",
    data: { lit: l.source === selected || l.target === selected, stale: l.stale },
  }));

  const flow = useReactFlow();
  const fit = (duration: number) => spots.size && flow.fitBounds(bounds(spots), { padding: 0.12, duration });
  useEffect(() => {
    fit(400);
  }, [shape]);

  return (
    <ReactFlow
      nodes={nodes}
      onNodesChange={onNodesChange}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      nodeOrigin={[0.5, 0.5]}
      colorMode="dark"
      nodesDraggable={false}
      nodesConnectable={false}
      onNodeClick={(_, node) => select(node.id)}
      onInit={() => fit(0)} // the effect above runs before React Flow can move the view
      minZoom={0.2}
    >
      <Background gap={28} size={1} />
    </ReactFlow>
  );
}

/** A node: solid if it has a page, a hollow outline if it's only a router. */
function Thought({ data }: NodeProps<Node<ThoughtData>>) {
  const classes = ["thought", data.router ? "router" : "page", data.selected && "selected", data.name === ROOT && "root"];
  return (
    <div className={classes.filter(Boolean).join(" ")} title={data.router ? "Router: links only" : "Page"}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
      <span className="name">{data.name}</span>
      <span className="links">{data.links}</span>
    </div>
  );
}

/** A straight line between centres: in a web of cross-links there's no side a link naturally leaves from. */
function Tie({ id, source, target, data }: EdgeProps<Edge<TieData>>) {
  const from = useInternalNode(source);
  const to = useInternalNode(target);
  if (!from || !to) return null;
  const [a, b] = [centre(from), centre(to)];
  const classes = [data?.lit && "lit", data?.stale && "stale"].filter(Boolean).join(" ");
  return <BaseEdge id={id} path={`M${a.x},${a.y} L${b.x},${b.y}`} className={classes} />;
}

function centre(node: InternalNode) {
  const { x, y } = node.internals.positionAbsolute;
  return { x: x + (node.measured.width ?? NODE.width) / 2, y: y + (node.measured.height ?? NODE.height) / 2 };
}

/** A node's page, editable; where it links and what links to it; and how it came to be, in the owner's words. */
function Page({ name, graph, select }: { name: string; graph?: TwinGraph; select: (name: string) => void }) {
  const [node, setNode] = useState<TwinNode | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const dirty = node !== null && node.name === name && draft !== node.page;
  const version = JSON.stringify(graph?.links) + JSON.stringify(graph?.nodes);

  const load = () =>
    api.twinNode(name).then((found) => {
      setNode(found);
      setDraft(found.page);
    }, (e: Error) => setError(e.message));
  useEffect(() => {
    setError("");
    if (!dirty) load(); // the writer may have changed it; never over what the owner is typing
  }, [name, version]);

  if (!node || node.name !== name) return <p className="quiet">{error || "Loading…"}</p>;
  return (
    <div className="twin-page">
      <h2>{node.name}</h2>
      {node.aliases.length > 0 && <p className="quiet">Also {node.aliases.join(", ")}</p>}
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        placeholder="A router: no page, only links. Write one to make it a page."
      />
      <div className="actions">
        <button className="approve" disabled={!dirty} onClick={() => api.editTwinNode(name, draft).then(load)}>
          Save
        </button>
        <button disabled={!dirty} onClick={() => setDraft(node.page)}>
          Discard changes
        </button>
        {node.name !== ROOT && (
          <button
            onClick={() =>
              confirm(`Delete ${node.name} and its links?`) &&
              api.deleteTwinNode(name).then(() => select(ROOT), (e: Error) => setError(e.message))
            }
          >
            Delete
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      <h3>Links</h3>
      {node.from.length + node.to.length === 0 && <p className="quiet">None yet.</p>}
      <ul className="ties">
        {node.from.map((l) => (
          <li key={`>${l.target}`} role="button" tabIndex={0} onClick={() => select(l.target)} onKeyDown={(e) => e.key === "Enter" && select(l.target)}>
            <span className="to">→ {l.target}</span> <span className="quiet">{l.relation}</span>
            <p className={l.stale ? "stale" : ""}>{l.preview}</p>
          </li>
        ))}
        {node.to.map((l) => (
          <li key={`<${l.source}`} role="button" tabIndex={0} onClick={() => select(l.source)} onKeyDown={(e) => e.key === "Enter" && select(l.source)}>
            <span className="to">← {l.source}</span> <span className="quiet">{l.relation}</span>
          </li>
        ))}
      </ul>

      <h3>History</h3>
      <ol className="history">
        {node.history.map((c, i) => (
          <li key={i}>
            <time>{new Date(c.time * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</time>{" "}
            {c.what} <span className="quiet">· {c.origin}</span>
            {c.said && <q>{c.said}</q>}
          </li>
        ))}
      </ol>
    </div>
  );
}

type Spot = SimulationNodeDatum & { id: string };

/** A force layout: links pull, nodes push apart, You held in the middle; hubs settle where their links meet. Run to
 * rest at once, not animated: the picture is the result, not the physics. */
function layout(graph: TwinGraph): Map<string, { x: number; y: number }> {
  const spots: Spot[] = graph.nodes.map((n) => (n.name === ROOT ? { id: n.name, fx: 0, fy: 0 } : { id: n.name }));
  forceSimulation(spots)
    .force("link", forceLink<Spot, { source: string; target: string }>(graph.links.map((l) => ({ ...l }))).id((s) => s.id).distance(200))
    .force("charge", forceManyBody().strength(-1400))
    .force("collide", forceCollide(NODE.width * 0.75))
    .force("x", forceX(0).strength(0.04))
    .force("y", forceY(0).strength(0.04))
    .stop()
    .tick(300);
  return new Map(spots.map((s) => [s.id, { x: s.x ?? 0, y: s.y ?? 0 }]));
}

function bounds(spots: Map<string, { x: number; y: number }>) {
  const xs = [...spots.values()].map((p) => p.x);
  const ys = [...spots.values()].map((p) => p.y);
  const [left, top] = [Math.min(...xs) - NODE.width / 2, Math.min(...ys) - NODE.height / 2];
  return { x: left, y: top, width: Math.max(...xs) + NODE.width / 2 - left, height: Math.max(...ys) + NODE.height / 2 - top };
}
