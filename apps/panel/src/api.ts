// The agent's control panel API (apps/agent/src/agent/channels/panel.py).

export type Task = {
  id: number;
  conversation: string;
  goal: string;
  status: string;
  open: boolean;
  report: string;
};
export type Step = { id: number; time: number; text: string; screen: boolean };
export type Line = { id: number; time: number; sender: "owner" | "archangel"; text: string; image: string | null };
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
  tasks: () => call<Task[]>("GET", "/tasks"),
  steps: (task: number, after: number) => call<Step[]>("GET", `/tasks/${task}/steps?after=${after}`),
  answer: (task: number, text: string, approve: boolean) =>
    call<{ result: string }>("POST", `/tasks/${task}/answer`, { text, approve }),
  cancel: (task: number) => call<{ result: string }>("POST", `/tasks/${task}/cancel`),
  chat: (after: number) => call<Line[]>("GET", `/chat?after=${after}`),
  say: (text: string) => call<null>("POST", "/chat", { text }),
  prompts: () => call<Prompt[]>("GET", "/prompts"),
  editPrompt: (name: string, text: string) => call<Prompt>("PUT", `/prompts/${name}`, { text }),
  resetPrompt: (name: string) => call<Prompt>("DELETE", `/prompts/${name}`),
};

export const screenUrl = (step: number) => `/api/steps/${step}/screen`;
export const lineImageUrl = (line: number) => `/api/chat/${line}/image`;
