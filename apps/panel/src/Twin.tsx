import {
  Background,
  BaseEdge,
  type Connection,
  type Edge,
  type EdgeProps,
  Handle,
  type InternalNode,
  type Node,
  type NodeProps,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useConnection,
  useInternalNode,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useActivity } from "./activity";
import { api, type TwinGraph, type TwinLink, type TwinNode } from "./api";
import { Redrawn } from "./Cockpit";
import { usePoll } from "./poll";

const ROOT = "You";
const READ_SECONDS = 8; // how long a node the reader opened stays lit
const NODE = { width: 132, height: 40 }; // as in styles.css

/** What the side pane shows: a node's page, or one link between two. */
type Selection = { node: string } | { link: { source: string; target: string } };

/** The context twin: the owner's second brain, a graph of pages the agents read before acting (docs/twin.md). The
 * writer grows it; here the owner can too: add nodes, drag one onto another to link them, edit any of it. */
export function Twin() {
  const graph = usePoll(api.twin);
  const [chosen, select] = useState<Selection>({ node: ROOT });
  const selection = graph ? still(chosen, graph) : chosen;

  return (
    <>
      <section className="twin-graph">
        {graph && (
          <Redrawn>
            <ReactFlowProvider>
              <Brain graph={graph} selection={selection} select={select} />
            </ReactFlowProvider>
          </Redrawn>
        )}
      </section>
      <section className="twin-node">
        {"node" in selection ? (
          <NodePage name={selection.node} graph={graph} select={select} />
        ) : (
          <LinkPage link={graph?.links.find((l) => same(l, selection.link))} select={select} />
        )}
      </section>
    </>
  );
}

/** The selection, if it's still there; else the root (a node deleted or renamed, here or by the writer). */
function still(selection: Selection, graph: TwinGraph): Selection {
  if ("node" in selection) return graph.nodes.some((n) => n.name === selection.node) ? selection : { node: ROOT };
  return graph.links.some((l) => same(l, selection.link)) ? selection : { node: selection.link.source };
}

const same = (a: { source: string; target: string }, b: { source: string; target: string }) =>
  a.source === b.source && a.target === b.target;

type ThoughtData = { name: string; router: boolean; links: number; selected: boolean; reading: boolean };
type TieData = { lit: boolean; stale: boolean; selected: boolean };

const nodeTypes = { thought: Thought };
const edgeTypes = { tie: Tie };

function Brain({ graph, selection, select }: { graph: TwinGraph; selection: Selection; select: (s: Selection) => void }) {
  const shape = graph.nodes.map((n) => n.name).join() + "|" + graph.links.map((l) => `${l.source}>${l.target}`).join();
  // Placed when a node or link comes or goes: the new ones find a spot, the rest stay where you've seen them, until
  // the owner asks for a tidy.
  const placed = useRef(new Map<string, Point>());
  const [tidied, setTidied] = useState(0);
  const spots = useMemo(() => (placed.current = layout(graph, placed.current)), [shape, tidied]);
  const tidy = () => {
    placed.current = new Map();
    setTidied((n) => n + 1);
  };
  const degree = (name: string) => graph.links.filter((l) => l.source === name || l.target === name).length;
  const node = "node" in selection ? selection.node : null;
  const link = "link" in selection ? selection.link : null;
  // What the reader is opening right now, briefing a task or answering an agent: lit as it goes.
  const notices = useActivity();
  const reading = new Set(
    notices.filter((n) => n.target === "twin" && Date.now() / 1000 - n.time < READ_SECONDS).map((n) => n.text.toLowerCase()),
  );
  const lit = graph.nodes.filter((n) => reading.has(n.name.toLowerCase()) || n.aliases.some((a) => reading.has(a.toLowerCase())));
  const readingNow = lit.map((n) => n.name).join();

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
        data: { name: n.name, router: n.router, links: degree(n.name), selected: n.name === node, reading: lit.includes(n) },
      }));
    });
  }, [spots, routers, node, readingNow]);
  const edges: Edge<TieData>[] = graph.links.map((l) => ({
    id: `${l.source}>${l.target}`,
    source: l.source,
    target: l.target,
    type: "tie",
    data: { lit: l.source === node || l.target === node, stale: l.stale, selected: !!link && same(l, link) },
  }));

  const flow = useReactFlow();
  const fit = (duration: number) => spots.size && flow.fitBounds(bounds(spots), { padding: 0.12, duration });
  useEffect(() => {
    fit(400);
  }, [spots]);

  const connect = ({ source, target }: Connection) => {
    if (source === target) return;
    const existing = graph.links.find((l) => same(l, { source, target }));
    const made = existing ? Promise.resolve() : api.linkTwin({ source, target, relation: "", preview: "" });
    made.then(() => select({ link: { source, target } }), (e: Error) => alert(e.message));
  };

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
      deleteKeyCode={null} // deleting is a button on the node's page, kept by the agent; never a stray key here
      onConnect={connect}
      connectionLineStyle={{ stroke: "var(--accent)", strokeWidth: 1.5 }}
      onNodeClick={(_, n) => select({ node: n.id })}
      onEdgeClick={(_, e) => select({ link: { source: e.source, target: e.target } })}
      onInit={() => fit(0)} // the effect above runs before React Flow can move the view
      minZoom={0.2}
    >
      <Background gap={28} size={1} />
      <Panel position="top-left">
        <div className="graph-tools">
          <AddNode added={(name) => select({ node: name })} />
          <button className="small" onClick={tidy} title="Lay the whole graph out afresh">
            Tidy
          </button>
        </div>
      </Panel>
      <Panel position="bottom-left" className="twin-hint">
        Drag a node's square onto another to link them
      </Panel>
    </ReactFlow>
  );
}

function AddNode({ added }: { added: (name: string) => void }) {
  const [name, setName] = useState<string | null>(null);
  const [error, setError] = useState("");
  const add = (e: FormEvent) => {
    e.preventDefault();
    const wanted = name?.trim();
    if (!wanted) return setName(null);
    api.addTwinNode(wanted).then(
      () => (setName(null), setError(""), added(wanted)),
      (e: Error) => setError(e.message),
    );
  };
  if (name === null) return <button onClick={() => setName("")}>+ Node</button>;
  return (
    <form className="add-node" onSubmit={add}>
      <input
        autoFocus
        value={name}
        placeholder="Name, e.g. Flights"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && (setName(null), setError(""))}
      />
      <button className="approve">Add</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

/** A node: solid if it has a page, a dashed outline if it's only a router. Its square (the grip) starts a link; while one is being
 * drawn, every other node is a target as a whole. */
function Thought({ id, data }: NodeProps<Node<ThoughtData>>) {
  const connection = useConnection();
  const target = connection.inProgress && connection.fromNode.id !== id;
  const classes = [
    "thought",
    data.router ? "router" : "page",
    data.selected && "selected",
    data.name === ROOT && "root",
    target && "target",
    data.reading && "reading",
  ];
  return (
    <div className={classes.filter(Boolean).join(" ")} title={data.router ? "Router: links only" : "Page"}>
      <Handle type="source" position={Position.Right} className="grip" />
      <Handle type="target" position={Position.Left} className="whole" isConnectableStart={false} />
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
  const classes = [data?.lit && "lit", data?.stale && "stale", data?.selected && "chosen"].filter(Boolean).join(" ");
  return <BaseEdge id={id} path={`M${a.x},${a.y} L${b.x},${b.y}`} className={classes} interactionWidth={16} />;
}

function centre(node: InternalNode) {
  const { x, y } = node.internals.positionAbsolute;
  return { x: x + (node.measured.width ?? NODE.width) / 2, y: y + (node.measured.height ?? NODE.height) / 2 };
}

/** A node's page, editable; its names; where it links and what links to it; how it came to be, in the owner's words. */
function NodePage({ name, graph, select }: { name: string; graph?: TwinGraph; select: (s: Selection) => void }) {
  const [node, setNode] = useState<TwinNode | null>(null);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [alias, setAlias] = useState("");
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
    setRenaming(null);
  }, [name]);
  useEffect(() => {
    if (!dirty) load(); // the writer may have changed it; never over what the owner is typing
  }, [name, version]);
  const run = (edit: Promise<unknown>, then: () => void = load) => edit.then(() => (setError(""), then()), (e: Error) => setError(e.message));

  if (!node || node.name !== name) return <p className="quiet">{error || "Loading…"}</p>;
  const root = node.name === ROOT;
  return (
    <div className="twin-page">
      {renaming === null ? (
        <div className="title">
          <h2>{node.name}</h2>
          {!root && (
            <button className="small" onClick={() => setRenaming(node.name)}>
              Rename
            </button>
          )}
        </div>
      ) : (
        <form
          className="title"
          onSubmit={(e) => {
            e.preventDefault();
            const to = renaming.trim();
            if (!to || to === node.name) return setRenaming(null);
            run(api.renameTwinNode(name, to), () => (setRenaming(null), select({ node: to })));
          }}
        >
          <input autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setRenaming(null)} />
          <button className="approve small">Save</button>
        </form>
      )}

      <div className="aliases">
        {node.aliases.map((a) => (
          <span key={a} className="alias">
            {a}
            <button aria-label={`Remove ${a}`} onClick={() => run(api.unaliasTwin(a))}>
              ×
            </button>
          </span>
        ))}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (alias.trim()) run(api.aliasTwinNode(name, alias.trim()), () => (setAlias(""), load()));
          }}
        >
          <input value={alias} placeholder="+ alias, e.g. Mum" onChange={(e) => setAlias(e.target.value)} />
        </form>
      </div>

      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        placeholder="A router: no page, only links. Write one to make it a page."
      />
      <div className="actions">
        <button className="approve" disabled={!dirty} onClick={() => run(api.editTwinNode(name, draft))}>
          Save
        </button>
        <button disabled={!dirty} onClick={() => setDraft(node.page)}>
          Discard changes
        </button>
        {!root && (
          <button onClick={() => confirm(`Delete ${node.name} and its links?`) && run(api.deleteTwinNode(name), () => select({ node: ROOT }))}>
            Delete
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}

      <h3>Links</h3>
      {node.from.length + node.to.length === 0 && <p className="quiet">None yet. Drag this node's square onto another.</p>}
      <ul className="ties">
        {[...node.from.map((l) => ({ l, other: l.target, arrow: "→" })), ...node.to.map((l) => ({ l, other: l.source, arrow: "←" }))].map(
          ({ l, other, arrow }) => (
            <li key={`${l.source}>${l.target}`}>
              <button className="to" onClick={() => select({ node: other })}>
                {arrow} {other}
              </button>{" "}
              <span className="quiet">{l.relation}</span>
              <button className="small edit" onClick={() => select({ link: l })}>
                Edit
              </button>
              {arrow === "→" && <Preview link={l} />}
            </li>
          ),
        )}
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

function Preview({ link }: { link: TwinLink }) {
  if (!link.preview) return <p className="quiet">No preview yet.</p>;
  return <p className={link.stale ? "stale" : ""}>{link.preview}</p>;
}

/** One link: what it is from its source's side, and the few words an agent reads before deciding to follow it. */
function LinkPage({ link, select }: { link?: TwinLink; select: (s: Selection) => void }) {
  const [relation, setRelation] = useState("");
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");
  const key = link && `${link.source}>${link.target}`;
  useEffect(() => {
    setRelation(link?.relation ?? "");
    setPreview(link?.preview ?? "");
    setError("");
  }, [key, link?.relation, link?.preview]);

  if (!link) return <p className="quiet">Loading…</p>;
  const dirty = relation !== link.relation || preview !== link.preview;
  const words = preview.trim().split(/\s+/).filter(Boolean).length;
  return (
    <form
      className="twin-page"
      onSubmit={(e) => {
        e.preventDefault();
        api.linkTwin({ ...link, relation: relation.trim(), preview: preview.trim() }).then(() => setError(""), (e: Error) => setError(e.message));
      }}
    >
      <div className="title">
        <h2>Link</h2>
      </div>
      <p className="ends">
        <button type="button" className="to" onClick={() => select({ node: link.source })}>
          {link.source}
        </button>
        <span className="quiet"> → </span>
        <button type="button" className="to" onClick={() => select({ node: link.target })}>
          {link.target}
        </button>
      </p>
      <label>
        Relation, from {link.source}'s side
        <input value={relation} placeholder="e.g. mother, uses, travels by" onChange={(e) => setRelation(e.target.value)} />
      </label>
      <label>
        Preview: what's at {link.target} that matters from here
        <input value={preview} placeholder="5–10 words, e.g. birthday 14 May, loves lilies" onChange={(e) => setPreview(e.target.value)} />
        <span className={words > 10 ? "count over" : "count"}>{words} words</span>
      </label>
      {link.stale && <p className="stale">{link.target}'s page changed since this preview was written.</p>}
      <div className="actions">
        <button className="approve" disabled={!dirty && !link.stale}>
          Save
        </button>
        <button type="button" disabled={!dirty} onClick={() => (setRelation(link.relation), setPreview(link.preview))}>
          Discard changes
        </button>
        <button
          type="button"
          onClick={() =>
            confirm(`Unlink ${link.source} → ${link.target}?`) &&
            api.unlinkTwin(link.source, link.target).then(() => select({ node: link.source }), (e: Error) => setError(e.message))
          }
        >
          Unlink
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

type Point = { x: number; y: number };
type Spot = SimulationNodeDatum & { id: string };

/** A force layout: links pull, nodes push apart, You held in the middle; hubs settle where their links meet. Nodes
 * already `kept` stay put and only new ones move, starting beside something they link to. Run to rest at once, not
 * animated: the picture is the result, not the physics. */
function layout(graph: TwinGraph, kept = new Map<string, Point>()): Map<string, Point> {
  const neighbour = (name: string) =>
    graph.links.map((l) => kept.get(l.source === name ? l.target : l.target === name ? l.source : "")).find(Boolean);
  const spots: Spot[] = graph.nodes.map((n) => {
    const at = kept.get(n.name) ?? (n.name === ROOT ? { x: 0, y: 0 } : undefined);
    if (at) return { id: n.name, x: at.x, y: at.y, fx: at.x, fy: at.y };
    const near = neighbour(n.name);
    return near ? { id: n.name, x: near.x + 40, y: near.y + 90 } : { id: n.name };
  });
  const settle = forceSimulation(spots)
    .force("link", forceLink<Spot, { source: string; target: string }>(graph.links.map((l) => ({ ...l }))).id((s) => s.id).distance(200))
    .force("charge", forceManyBody().strength(-1400))
    .force("collide", forceCollide(NODE.width * 0.75).iterations(4)) // pinned nodes can't give way
    .force("x", forceX(0).strength(0.04))
    .force("y", forceY(0).strength(0.04))
    .stop()
    .tick(300);
  // Then only make room: the pull of links can hold a new node on top of a pinned one.
  settle.force("link", null).force("charge", null).force("x", null).force("y", null).alpha(1).tick(120);
  return new Map(spots.map((s) => [s.id, { x: s.x ?? 0, y: s.y ?? 0 }]));
}

function bounds(spots: Map<string, Point>) {
  const xs = [...spots.values()].map((p) => p.x);
  const ys = [...spots.values()].map((p) => p.y);
  const [left, top] = [Math.min(...xs) - NODE.width / 2, Math.min(...ys) - NODE.height / 2];
  return { x: left, y: top, width: Math.max(...xs) + NODE.width / 2 - left, height: Math.max(...ys) + NODE.height / 2 - top };
}
