// Small sounds for the panel (public/sounds/<cue>.wav, chosen by the owner, made with earshot): a press, a tab,
// scrolling, a switch, a message each way, approvals, a task that needs the owner or finished, the cockpit's traffic.
// Web Audio, not <audio>: a click has to land with the press, and decoded buffers play at once.

export type Cue =
  | "press" | "nav" | "scroll" | "toggleOn" | "toggleOff" | "send" | "receive"
  | "approve" | "decline" | "needs" | "done" | "error" | "tx" | "rx";

const KEY = "archangel.sound";
let on = localStorage.getItem(KEY) !== "off";
const listeners = new Set<() => void>();
let context: AudioContext | undefined;
const buffers = new Map<Cue, Promise<AudioBuffer | undefined>>();

export const soundOn = () => on;

export function setSoundOn(next: boolean) {
  on = next;
  localStorage.setItem(KEY, next ? "on" : "off");
  listeners.forEach((changed) => changed());
}

/** For useSyncExternalStore. */
export function subscribeSound(changed: () => void) {
  listeners.add(changed);
  return () => listeners.delete(changed);
}

export async function play(cue: Cue) {
  if (!on) return;
  // Browsers only allow audio after the first press, so the context starts then.
  context ??= new AudioContext();
  if (context.state === "suspended") await context.resume();
  if (!buffers.has(cue)) {
    buffers.set(
      cue,
      fetch(`/sounds/${cue}.wav`)
        .then((response) => response.arrayBuffer())
        .then((data) => context!.decodeAudioData(data))
        .catch(() => undefined),
    );
  }
  const decoded = await buffers.get(cue);
  if (!decoded) return;
  const node = context.createBufferSource();
  node.buffer = decoded;
  node.connect(context.destination);
  node.start();
}

/** Sounds that come in floods (scrolling, the cockpit's traffic) play at most this often. */
export function throttled(gapMs: number, sound: () => void) {
  let last = 0;
  return () => {
    const now = performance.now();
    if (now - last < gapMs) return;
    last = now;
    sound();
  };
}

/** Presses and scrolling anywhere. `data-sound` on an element picks another cue, or "" for none. */
export function listen() {
  document.addEventListener(
    "pointerdown",
    (event) => {
      const target = (event.target as Element).closest("button, a, [data-sound]");
      if (!target || (target as HTMLButtonElement).disabled) return;
      const chosen = target.getAttribute("data-sound");
      if (chosen === "") return;
      play(chosen ? (chosen as Cue) : target.closest("nav") ? "nav" : "press");
    },
    { capture: true },
  );
  // Only the owner's own scrolling: the panel scrolls itself too (new chat lines, the cockpit following along).
  let touched = 0;
  let held = false;
  const touch = () => (touched = performance.now());
  for (const kind of ["wheel", "touchmove", "keydown"]) addEventListener(kind, touch, { capture: true, passive: true });
  addEventListener("pointerdown", () => (held = true), { capture: true });
  addEventListener("pointerup", () => (held = false), { capture: true });
  const scrolled = throttled(70, () => play("scroll"));
  document.addEventListener(
    "scroll",
    () => (held || performance.now() - touched < 250) && scrolled(),
    { capture: true, passive: true },
  );
}
