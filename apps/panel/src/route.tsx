import { useSyncExternalStore } from "react";

/** The panel's pages are real paths (/tasks/12, /connectors), so links, reloads and the back button just work.
 * The agent serves the app on each of them (PAGES in apps/agent/src/agent/web.py). */
export type Page = "chat" | "tasks" | "cockpit" | "prompts" | "connectors";

const moved = "archangel:navigate";

export function useRoute(): { page: Page | null; id: number | undefined } {
  const path = useSyncExternalStore(
    (changed) => {
      addEventListener("popstate", changed);
      addEventListener(moved, changed);
      return () => {
        removeEventListener("popstate", changed);
        removeEventListener(moved, changed);
      };
    },
    () => location.pathname,
  );
  const [page, id] = path.split("/").slice(1);
  return { page: (page as Page) || null, id: id ? Number(id) : undefined };
}

export function go(path: string): void {
  if (path === location.pathname) return;
  history.pushState(null, "", path);
  dispatchEvent(new Event(moved));
}

/** A plain link that changes page in place; a modified click (new tab) still works as a browser link would. */
export function Link({ to, className, children }: { to: string; className?: string; children: React.ReactNode }) {
  return (
    <a
      href={to}
      className={className}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        go(to);
      }}
    >
      {children}
    </a>
  );
}
