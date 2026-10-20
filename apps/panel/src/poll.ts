import { useEffect, useRef, useState } from "react";

const EVERY_MS = 1000;

type Shared = { value: unknown; watchers: Set<(value: unknown) => void>; timer: number };
const polls = new Map<() => Promise<unknown>, Shared>();

/** `load()` now and every second after; the latest result, or undefined until the first arrives. Everything polling
 * the same `load` (the task list, for the Tasks page and the panel's sounds) shares one request. */
export function usePoll<T>(load: () => Promise<T>): T | undefined {
  const [value, setValue] = useState<T | undefined>(() => polls.get(load)?.value as T | undefined);
  useEffect(() => {
    let shared = polls.get(load);
    if (!shared) {
      const fresh: Shared = { value: undefined, watchers: new Set(), timer: 0 };
      const run = () =>
        load().then((v) => {
          fresh.value = v;
          fresh.watchers.forEach((watch) => watch(v));
        }, () => {});
      run();
      fresh.timer = setInterval(run, EVERY_MS);
      polls.set(load, (shared = fresh));
    }
    const watch = (v: unknown) => setValue(v as T);
    shared.watchers.add(watch);
    return () => {
      shared.watchers.delete(watch);
      if (!shared.watchers.size) {
        clearInterval(shared.timer);
        polls.delete(load);
      }
    };
  }, []);
  return value;
}

/** An append-only list (steps, chat lines): fetches only what's newer than the last item it has. */
export function useFeed<T extends { id: number }>(load: (after: number) => Promise<T[]>, key: unknown = null): T[] {
  const [items, setItems] = useState<T[]>([]);
  const last = useRef(0);
  useEffect(() => {
    let live = true;
    let busy = false;
    setItems([]);
    last.current = 0;
    const run = async () => {
      if (busy) return;
      busy = true;
      try {
        const fresh = await load(last.current);
        if (live && fresh.length) {
          last.current = fresh[fresh.length - 1].id;
          setItems((old) => [...old, ...fresh]);
        }
      } catch {
        // The agent is restarting or stopped; the next tick tries again.
      } finally {
        busy = false;
      }
    };
    run();
    const timer = setInterval(run, EVERY_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [key]);
  return items;
}
