import { useEffect, useRef, useState } from "react";

const EVERY_MS = 1000;

/** `load()` now and every second after; the latest result, or undefined until the first arrives. */
export function usePoll<T>(load: () => Promise<T>, key: unknown = null): [T | undefined, () => void] {
  const [value, setValue] = useState<T>();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const run = () => load().then((v) => live && setValue(v), () => {});
    run();
    const timer = setInterval(run, EVERY_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [key, tick]);
  return [value, () => setTick((t) => t + 1)];
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
