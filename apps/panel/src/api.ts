// The agent's control panel API (apps/agent/src/agent/channels/panel.py).

export type Task = {
  id: number;
  goal: string;
  kind: "phone" | "research" | "deep research" | "connectors";
  /** A research report's web page, at /reports/{id}. */
  page: boolean;
  status: string;
  open: boolean;
  report: string;
  reportTo: string;
};
export type Step = { id: number; time: number; text: string; screen: string | null };
export type Line = {
  id: number;
  time: number;
  channel: string;
  sender: "owner" | "archangel";
  text: string;
  images: string[];
};
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
/** Where to send the owner to sign in; null if they still are. */
type SignIn = { signIn: string | null };

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
  say: (text: string) => call<null>("POST", "/messages", { text }),
  tasks: () => call<Task[]>("GET", "/tasks"),
  steps: (task: number, after: number) => call<Step[]>("GET", `/tasks/${task}/steps?after=${after}`),
  prompts: () => call<Prompt[]>("GET", "/prompts"),
  editPrompt: (name: string, text: string) => call<Prompt>("PUT", `/prompts/${name}`, { text }),
  resetPrompt: (name: string) => call<Prompt>("DELETE", `/prompts/${name}`),
  connectors: () => call<{ services: Service[]; connectors: Connector[] }>("GET", "/connectors"),
  addConnector: (kind: string, settings: Record<string, string>, profile: string) =>
    call<SignIn>("POST", "/connectors", { kind, settings, profile }),
  signIn: (name: string) => call<SignIn>("POST", `/connectors/${name}/sign-in`),
  removeConnector: (name: string) => call<null>("DELETE", `/connectors/${name}`),
};

/** Images expire after a few days: `hideExpired` as `onError` shows nothing rather than a broken icon. */
export const imageUrl = (name: string) => `/api/images/${name}`;
export const hideExpired = (e: { currentTarget: HTMLImageElement }) => {
  e.currentTarget.style.display = "none";
};
