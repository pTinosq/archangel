import { Background, type Edge, type Node, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow } from "@xyflow/react";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, type EvalCase, type EvalGraph, type EvalReport, type EvalRun, type EvalSummary } from "./api";
import { Redrawn } from "./Cockpit";
import { usePoll } from "./poll";
import { bounds, layout, type ThoughtData, Thought, type TieData, Tie } from "./Twin";

const EVERY_MS = 1000;

/** The context twin's evals (apps/agent/evals/twin), on a local agent only: run them, see each case's pass rate
 * against the run before, and open any run: what it was given, every check, what the reader said, what the writer
 * changed, and the graph it ended with. */
export function Evals() {
  const index = usePoll(api.evals);
  const [chosen, choose] = useState<string | null>(null);
  const readable = index?.results.filter((r) => !r.unreadable) ?? [];
  const id = chosen ?? readable[0]?.id ?? null;
  const report = useReport(id);
  const before = useReport(readable[readable.findIndex((r) => r.id === id) + 1]?.id ?? null);
  const [open, setOpen] = useState<{ case: string; run: number } | null>(null);
  useEffect(() => {
    setOpen(null);
  }, [id]);

  if (index === undefined) return <section className="eval-list quiet">Evals run on a local agent only (just agent).</section>;
  const opened = open && report?.results.find((r) => r.case === open.case && r.run === open.run);
  return (
    <>
      <section className="eval-list">
        <h2>Evals</h2>
        <RunForm cases={index.cases} started={choose} />
        <ul>
          {index.results.map((r) => (
            <li key={r.id} className={r.id === id ? "selected" : ""} onClick={() => !r.unreadable && choose(r.id)}>
              <Summary summary={r} />
            </li>
          ))}
        </ul>
        {index.results.length === 0 && <p className="quiet">None yet.</p>}
      </section>
      <section className="eval-report">
        {!report ? (
          <p className="quiet">{id ? "Loading…" : "Run the evals to see them here."}</p>
        ) : (
          <>
            <Grid report={report} before={before} open={open} select={setOpen} />
            {opened && <RunDetail key={`${opened.case}/${opened.run}`} run={opened} report={report} />}
          </>
        )}
      </section>
    </>
  );
}

/** An eval run's whole file, again every second until it's finished. */
function useReport(id: string | null): EvalReport | null {
  const [report, setReport] = useState<EvalReport | null>(null);
  useEffect(() => {
    setReport(null);
    if (!id) return;
    let done = false;
    const load = () =>
      api.evalReport(id).then((r) => {
        setReport(r);
        done = r.finished !== null;
      }, () => {});
    load();
    const timer = setInterval(() => !done && load(), EVERY_MS);
    return () => clearInterval(timer);
  }, [id]);
  return report;
}

function RunForm({ cases, started }: { cases: string[]; started: (id: string) => void }) {
  const [filter, setFilter] = useState("");
  const [runs, setRuns] = useState(3);
  const [error, setError] = useState("");
  const run = (e: FormEvent) => {
    e.preventDefault();
    api.runEvals(filter, runs).then((r) => (setError(""), started(r.id)), (e: Error) => setError(e.message));
  };
  const count = cases.filter((c) => c.includes(filter)).length;
  return (
    <form className="eval-run" onSubmit={run}>
      <select value={filter} onChange={(e) => setFilter(e.target.value)}>
        <option value="">All {cases.length} cases</option>
        {cases.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      <label>
        ×<input type="number" min={1} max={10} value={runs} onChange={(e) => setRuns(Number(e.target.value) || 1)} />
      </label>
      <button className="approve" disabled={!count}>
        Run
      </button>
      <p className="quiet">
        {count * runs} runs on the real model{error && <span className="error"> · {error}</span>}
      </p>
    </form>
  );
}

function Summary({ summary: r }: { summary: EvalSummary }) {
  if (r.unreadable) return <span className="quiet">{r.id} (unreadable)</span>;
  return (
    <>
      <div>
        <span className="score">
          {r.passed}/{r.done}
        </span>{" "}
        {r.finished === null ? <span className="status running">running {r.done}/{r.total}</span> : <Rate passed={r.passed} of={r.done} />}
      </div>
      <div className="quiet">
        {new Date(r.started * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })} · {r.model.split("/").pop()}
        {r.filter && ` · ${r.filter}`}
      </div>
    </>
  );
}

function Rate({ passed, of }: { passed: number; of: number }) {
  const rate = of ? passed / of : 0;
  return <span className={`rate ${rate === 1 ? "all" : rate >= 0.5 ? "most" : "few"}`}>{Math.round(rate * 100)}%</span>;
}

/** Cases down, runs across; each case's pass rate, and how it moved since the run before. */
function Grid({
  report,
  before,
  open,
  select,
}: {
  report: EvalReport;
  before: EvalReport | null;
  open: { case: string; run: number } | null;
  select: (o: { case: string; run: number }) => void;
}) {
  const rate = (r: EvalReport | null, name: string) => {
    const mine = r?.results.filter((x) => x.case === name) ?? [];
    return mine.length ? mine.filter((x) => x.passed).length / mine.length : null;
  };
  const passed = report.results.filter((r) => r.passed).length;
  return (
    <>
      <div className="eval-head">
        <h2>
          {passed}/{report.results.length} passed
        </h2>
        <span className="quiet">
          {report.model} · {report.runs} runs each
          {report.finished === null && ` · running, ${report.results.length}/${report.cases.length * report.runs}`}
          {before && ` · compared with ${before.id}`}
        </span>
      </div>
      <table className="eval-grid">
        <tbody>
          {report.cases.map((c) => {
            const now = rate(report, c.name);
            const then = rate(before, c.name);
            return (
              <tr key={c.name}>
                <th onClick={() => select({ case: c.name, run: firstFailing(report, c.name) })}>{c.name}</th>
                <td className="kind quiet">{kindOf(c)}</td>
                {Array.from({ length: report.runs }, (_, n) => {
                  const r = report.results.find((x) => x.case === c.name && x.run === n);
                  const chosen = open?.case === c.name && open.run === n;
                  return (
                    <td key={n}>
                      <button
                        className={["cell", r ? (r.passed ? "pass" : "fail") : "pending", chosen && "chosen"].filter(Boolean).join(" ")}
                        disabled={!r}
                        onClick={() => select({ case: c.name, run: n })}
                        title={r ? `${r.seconds}s` : "running"}
                      >
                        {r ? (r.passed ? "✓" : "✗") : "·"}
                      </button>
                    </td>
                  );
                })}
                <td>{now !== null && <Rate passed={Math.round(now * 100)} of={100} />}</td>
                <td className="delta">{now !== null && then !== null && now !== then && <Delta by={now - then} />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function Delta({ by }: { by: number }) {
  return <span className={by > 0 ? "up" : "down"}>{by > 0 ? "▲" : "▼"} {Math.round(Math.abs(by) * 100)}</span>;
}

const firstFailing = (report: EvalReport, name: string) =>
  report.results.filter((r) => r.case === name).sort((a, b) => Number(a.passed) - Number(b.passed) || a.run - b.run)[0]?.run ?? 0;

const kindOf = (c: EvalCase) => [c.say && "write", c.hear && "hear", c.brief && "brief", c.ask && "ask"].filter(Boolean).join(" + ");

/** One run: what went in, what was expected and what came out, and the graph it left. */
function RunDetail({ run, report }: { run: EvalRun; report: EvalReport }) {
  const spec = report.cases.find((c) => c.name === run.case)!;
  const start = report.graphs[spec.graph ?? "alex"];
  const [node, setNode] = useState<string | null>(null);
  const here = useRef<HTMLDivElement>(null);
  useEffect(() => {
    here.current?.scrollIntoView({ behavior: "smooth", block: "start" }); // returns a promise in some browsers: not a cleanup
  }, []);
  return (
    <div className="eval-run-detail" ref={here}>
      <div className="eval-graph">
        <div className="canvas">
          <Redrawn>
            <ReactFlowProvider>
              <RunGraph start={start} run={run} chosen={node} choose={setNode} />
            </ReactFlowProvider>
          </Redrawn>
        </div>
        <p className="legend quiet">
          <span className="added">added</span> <span className="changed">changed</span> <span className="opened">read</span> · click a node
          for its page
        </p>
      </div>
      <div className="eval-text">
        <h3>
          {run.case} · run {run.run + 1} · {run.seconds}s
        </h3>
        {run.error && <p className="error">{run.error}</p>}
        {spec.say?.map((said, i) => (
          <Block key={i} label={spec.say!.length > 1 ? `Said to the writer (${i + 1})` : "Said to the writer"}>
            {said}
          </Block>
        ))}
        {run.notes.some(Boolean) && <Block label="Writer's note">{run.notes.filter(Boolean).join("\n")}</Block>}
        {run.kind && <Block label={`Reader, ${run.kind}`}>{run.input}</Block>}
        {run.kind && (
          <Block label={`Answer · opened ${run.opened.length ? run.opened.join(", ") : "nothing"}`}>
            {run.answer || <span className="quiet">(nothing relevant)</span>}
          </Block>
        )}

        <h3>Checks</h3>
        <ul className="checks">
          {run.checks.map((c) => (
            <li key={c.name} className={c.passed ? "pass" : "fail"}>
              <span className="mark">{c.passed ? "✓" : "✗"}</span> {c.name}
              {c.why && <p className="quiet">{c.why}</p>}
            </li>
          ))}
        </ul>

        {node ? (
          <NodeBefore name={node} start={start} after={run.graph} />
        ) : (
          <>
            <h3>Writer's edits</h3>
            {run.changes.length === 0 && <p className="quiet">None.</p>}
            <ol className="edits">
              {run.changes.map((c, i) => (
                <li key={i}>
                  <button className="to" onClick={() => setNode(c.node)}>
                    {c.node}
                  </button>{" "}
                  <span className="quiet">{c.what}</span>
                  {c.what === "page" || c.what === "added" ? <Diff before={c.before} after={c.after} /> : c.after && <pre>{c.after}</pre>}
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </div>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="block">
      <p className="label">{label}</p>
      <pre>{children}</pre>
    </div>
  );
}

/** Line by line: what went, what came. Pages are a few bullets, so lines are the unit that matters. */
function Diff({ before, after }: { before: string; after: string }) {
  const old = new Set(before.split("\n"));
  const fresh = new Set(after.split("\n"));
  return (
    <pre className="diff">
      {before.split("\n").filter((l) => l.trim() && !fresh.has(l)).map((l, i) => (
        <span key={`-${i}`} className="gone">
          - {l}
          {"\n"}
        </span>
      ))}
      {after.split("\n").filter((l) => l.trim()).map((l, i) => (
        <span key={`+${i}`} className={old.has(l) ? "same" : "new"}>
          {old.has(l) ? "  " : "+ "}
          {l}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

/** A node's page as the run left it, against how it started. */
function NodeBefore({ name, start, after }: { name: string; start: EvalGraph; after: EvalGraph }) {
  const was = start.nodes.find((n) => n.name === name);
  const is = after.nodes.find((n) => n.name === name);
  const links = after.links.filter((l) => l.source === name);
  return (
    <>
      <h3>{name}</h3>
      {is ? <Diff before={was?.page ?? ""} after={is.page} /> : <p className="quiet">Gone by the end of the run.</p>}
      {links.length > 0 && (
        <ul className="eval-links">
          {links.map((l) => (
            <li key={l.target}>
              → {l.target} <span className="quiet">({l.relation})</span> {l.preview}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

const nodeTypes = { thought: Thought };
const edgeTypes = { tie: Tie };

/** The graph a run ended with, laid out like the Context twin tab, marked with what the run did. */
function RunGraph({ start, run, chosen, choose }: { start: EvalGraph; run: EvalRun; chosen: string | null; choose: (n: string | null) => void }) {
  const graph = run.graph.nodes ? run.graph : start;
  const spots = useMemo(() => layout(graph), [run]);
  const read = new Set(run.opened.map((o) => o.toLowerCase()));
  const mark = (n: EvalGraph["nodes"][number]): ThoughtData["mark"] => {
    const was = start.nodes.find((s) => s.name === n.name);
    if (!was) return "added";
    if (was.page !== n.page || run.changes.some((c) => c.node === n.name)) return "changed";
    if (read.has(n.name.toLowerCase()) || n.aliases.some((a) => read.has(a.toLowerCase()))) return "opened";
  };
  const degree = (name: string) => graph.links.filter((l) => l.source === name || l.target === name).length;
  // React Flow's own state, as on the Context twin tab: fresh node objects lose their measurements, and hide.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<ThoughtData>>([]);
  useEffect(() => {
    setNodes((old) => {
      const kept = new Map(old.map((n) => [n.id, n]));
      return graph.nodes.map((n) => ({
        ...kept.get(n.name),
        id: n.name,
        type: "thought",
        position: spots.get(n.name) ?? { x: 0, y: 0 },
        data: { name: n.name, router: !n.page.trim(), links: degree(n.name), selected: n.name === chosen, reading: false, mark: mark(n) },
      }));
    });
  }, [spots, chosen]);
  const edges: Edge<TieData>[] = graph.links.map((l) => ({
    id: `${l.source}>${l.target}`,
    source: l.source,
    target: l.target,
    type: "tie",
    data: { lit: l.source === chosen || l.target === chosen, stale: false, selected: false },
  }));
  const flow = useReactFlow();
  const fit = (duration: number) => spots.size && flow.fitBounds(bounds(spots), { padding: 0.1, duration });
  useEffect(() => {
    fit(300);
  }, [spots]);
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
      deleteKeyCode={null}
      onNodeClick={(_, n) => choose(n.id === chosen ? null : n.id)}
      onPaneClick={() => choose(null)}
      onInit={() => fit(0)}
      minZoom={0.2}
    >
      <Background gap={28} size={1} />
    </ReactFlow>
  );
}
