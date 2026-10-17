// The agent's control panel API (apps/agent/src/agent/channels/panel.py).

export type Task = {
  id: number;
  goal: string;
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

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status}`);
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
};

/** Images expire after a few days: `hideExpired` as `onError` shows nothing rather than a broken icon. */
export const imageUrl = (name: string) => `/api/images/${name}`;
export const hideExpired = (e: { currentTarget: HTMLImageElement }) => {
  e.currentTarget.style.display = "none";
};
