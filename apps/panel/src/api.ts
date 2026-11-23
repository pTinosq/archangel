// The agent's control panel API (apps/agent/src/agent/channels/panel.py).

export type Task = {
  id: number;
  goal: string;
  kind: "phone" | "pc" | "research" | "deep research" | "connectors" | "code";
  /** A research report's web page, at /reports/{id}. */
  page: boolean;
  /** Its report opens for anyone with the link; otherwise only signed in. */
  public: boolean;
  status: string;
  open: boolean;
  report: string;
  reportTo: string;
  /** The conversation thread that started it. */
  thread: number | null;
};
/** Something Archangel will do later (apps/agent/src/agent/schedule.py). */
export type Job = {
  id: number;
  /** Next due, in seconds. */
  due: number;
  /** What Archangel said it would do then. */
  what: string;
  /** The owner's words that asked for it; "" if it wasn't them. */
  said: string;
  /** Cron, in the owner's time, if it repeats; else "". */
  every: string;
  /** What its run last noted ("Still 3 of 7"); "" if nothing. */
  progress: string;
  reportTo: string;
  thread: number | null;
};
export type Step = { id: number; time: number; text: string; screen: string | null };
export type Line = {
  id: number;
  time: number;
  channel: string;
  sender: "owner" | "archangel";
  text: string;
  images: string[];
  /** Its conversation thread, once known: the owner's lines are filed when Archangel reads them. */
  thread: number | null;
};
/** One topic of the conversation (apps/agent/src/agent/threads). */
export type Thread = {
  id: number;
  title: string;
  /** Written once it's finished; "" until then. */
  summary: string;
  open: boolean;
  created: number;
  updated: number;
  /** Tokens of context its last answer took; at `full` it carries on in a new thread. */
  size: number;
  full: number;
  /** The thread it carries on from, once that one was full. */
  continues: number | null;
};
export type ThreadDetail = Thread & { lines: Line[]; tasks: Task[] };
export type Prompt = { name: string; text: string; default: string; edited: boolean };
/** One the Connectors page offers: what the owner enters to set it up, and where to get it. */
export type Service = { kind: string; title: string; needs: string[]; help: string };
export type Connector = {
  name: string;
  kind: string;
  title: string;
  account: string;
  /** "work", "personal": the agents choose accounts by it. */
  profile: string;
  signedIn: boolean;
};
/** A quick tool (live data the orchestrator answers from): on or off, and what it needs. A secret's value never
 * comes back: `set` says whether it's there. */
export type QuickTool = {
  name: string;
  title: string;
  about: string;
  enabled: boolean;
  /** On and set up: what the orchestrator actually has. */
  active: boolean;
  settings: {
    key: string;
    label: string;
    secret: boolean;
    required: boolean;
    set: boolean;
    value: string;
    /** A list setting (email's allowed senders): its entries are in `list`, and it's saved whole. */
    items: boolean;
    list: string[];
  }[];
};
/** The context twin's graph: every node (an empty page makes it a router) and link, without the pages. */
export type TwinGraph = {
  nodes: { name: string; aliases: string[]; router: boolean }[];
  links: TwinLink[];
};
/** `preview`: a few words on what's at the target. `stale`: its target changed since; the writer rewrites it. */
export type TwinLink = { source: string; target: string; relation: string; preview: string; stale: boolean };
export type TwinChange = { time: number; node: string; what: string; before: string; after: string; said: string; origin: string };
export type TwinNode = { name: string; aliases: string[]; page: string; from: TwinLink[]; to: TwinLink[]; history: TwinChange[] };
/** The context twin's evals (apps/agent/evals/twin), on a local agent only. One eval run, summed up. */
export type EvalSummary = {
  id: string;
  started: number;
  finished: number | null;
  model: string;
  filter: string;
  total: number;
  done: number;
  passed: number;
  unreadable?: boolean;
};
/** A graph as an eval saw it: pages included. */
export type EvalGraph = {
  nodes: { name: string; page: string; aliases: string[] }[];
  links: { source: string; target: string; relation: string; preview: string }[];
};
/** One case, as written in cases.toml. */
export type EvalCase = {
  name: string;
  graph?: string;
  say?: string[];
  hear?: string;
  brief?: string;
  ask?: string;
  include?: string[];
  exclude?: string[];
  nothing?: boolean;
  opens_at_most?: number;
  unchanged?: boolean;
  new_nodes_at_most?: number;
  judge?: string[];
};
export type EvalCheck = { name: string; passed: boolean; why: string };
export type EvalChange = { node: string; what: string; before: string; after: string; said: string };
/** One run of one case. */
export type EvalRun = {
  case: string;
  run: number;
  kind: "hear" | "brief" | "ask" | "";
  input: string;
  answer: string;
  opened: string[];
  notes: string[];
  changes: EvalChange[];
  checks: EvalCheck[];
  graph: EvalGraph;
  seconds: number;
  error: string;
  passed: boolean;
};
export type EvalReport = {
  id: string;
  started: number;
  finished: number | null;
  model: string;
  filter: string;
  runs: number;
  cases: EvalCase[];
  graphs: Record<string, EvalGraph>;
  results: EvalRun[];
};
/** What's running: `commit` "" when not deployed from GitHub; `started`, in seconds, when the deploy went live. */
export type Version = { commit: string; message: string; started: number };
/** Where to send the owner to sign in; null if they still are. */
export type SignIn = { signIn: string | null };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) location.reload(); // signed out: the page load goes to sign in
  if (!response.ok) {
    const detail = await response.json().then((body) => body.detail, () => null);
    throw new Error(typeof detail === "string" ? detail : `${method} ${path}: ${response.status}`);
  }
  return response.json();
}

export const api = {
  transcript: (after: number) => call<Line[]>("GET", `/transcript?after=${after}`),
  /** Everything the owner does here, buttons included, is a message in the one conversation. */
  say: (text: string, thread?: number) => call<null>("POST", "/messages", { text, thread }),
  tasks: () => call<Task[]>("GET", "/tasks"),
  version: () => call<Version>("GET", "/version"),
  /** Not a message: who can open a report is the owner's own setting. */
  makePublic: (task: number, isPublic: boolean) => call<{ public: boolean }>("PUT", `/tasks/${task}/public`, { public: isPublic }),
  schedule: () => call<Job[]>("GET", "/schedule"),
  steps: (task: number, after: number) => call<Step[]>("GET", `/tasks/${task}/steps?after=${after}`),
  threads: () => call<Thread[]>("GET", "/threads"),
  thread: (id: number) => call<ThreadDetail>("GET", `/threads/${id}`),
  prompts: () => call<Prompt[]>("GET", "/prompts"),
  editPrompt: (name: string, text: string) => call<Prompt>("PUT", `/prompts/${name}`, { text }),
  resetPrompt: (name: string) => call<Prompt>("DELETE", `/prompts/${name}`),
  connectors: () => call<{ services: Service[]; connectors: Connector[] }>("GET", "/connectors"),
  addConnector: (kind: string, settings: Record<string, string>, profile: string) =>
    call<SignIn>("POST", "/connectors", { kind, settings, profile }),
  signIn: (name: string) => call<SignIn>("POST", `/connectors/${name}/sign-in`),
  removeConnector: (name: string) => call<null>("DELETE", `/connectors/${name}`),
  tools: () => call<QuickTool[]>("GET", "/tools"),
  /** Only what's given changes; an empty setting clears it. */
  setTool: (name: string, change: { enabled?: boolean; settings?: Record<string, string | string[]> }) =>
    call<QuickTool[]>("PUT", `/tools/${name}`, change),
  evals: () => call<{ cases: string[]; results: EvalSummary[] }>("GET", "/evals"),
  evalReport: (id: string) => call<EvalReport>("GET", `/evals/${id}`),
  runEvals: (filter: string, runs: number) => call<{ id: string }>("POST", "/evals", { filter, runs }),
  twin: () => call<TwinGraph>("GET", "/twin"),
  twinNode: (name: string) => call<TwinNode>("GET", `/twin/nodes/${encodeURIComponent(name)}`),
  editTwinNode: (name: string, page: string) => call<null>("PUT", `/twin/nodes/${encodeURIComponent(name)}`, { page }),
  deleteTwinNode: (name: string) => call<null>("DELETE", `/twin/nodes/${encodeURIComponent(name)}`),
  addTwinNode: (name: string) => call<null>("POST", "/twin/nodes", { name }),
  renameTwinNode: (name: string, to: string) => call<null>("PUT", `/twin/nodes/${encodeURIComponent(name)}/name`, { name: to }),
  aliasTwinNode: (name: string, alias: string) => call<null>("POST", `/twin/nodes/${encodeURIComponent(name)}/aliases`, { name: alias }),
  unaliasTwin: (alias: string) => call<null>("DELETE", `/twin/aliases/${encodeURIComponent(alias)}`),
  /** Adds a link, or rewrites one's relation and preview. */
  linkTwin: (link: { source: string; target: string; relation: string; preview: string }) =>
    call<null>("PUT", "/twin/links", { source: link.source, target: link.target, relation: link.relation, preview: link.preview }),
  unlinkTwin: (source: string, target: string) =>
    call<null>("DELETE", `/twin/links?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`),
};

/** Images expire after a few days: `hideExpired` as `onError` shows nothing rather than a broken icon. */
export const imageUrl = (name: string) => `/api/images/${name}`;
export const hideExpired = (e: { currentTarget: HTMLImageElement }) => {
  e.currentTarget.style.display = "none";
};
