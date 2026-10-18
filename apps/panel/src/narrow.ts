import { useSyncExternalStore } from "react";

/** Phone-sized: one column at a time, the conversation its own tab. Matches the breakpoint in styles.css. */
const query = matchMedia("(max-width: 760px)");

export function useNarrow(): boolean {
  return useSyncExternalStore(
    (changed) => {
      query.addEventListener("change", changed);
      return () => query.removeEventListener("change", changed);
    },
    () => query.matches,
  );
}
