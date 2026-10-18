import { useEffect } from "react";
import { Chat } from "./Chat";
import { Cockpit } from "./Cockpit";
import { Connectors } from "./Connectors";
import { useNarrow } from "./narrow";
import { Prompts } from "./Prompts";
import { go, Link, type Page, useRoute } from "./route";
import { Tasks } from "./Tasks";

/** Served by the agent on the internet, behind GitHub sign-in; locally there's no one to sign out. */
const PUBLIC = !["localhost", "127.0.0.1"].includes(location.hostname);

export function App() {
  const narrow = useNarrow();
  const route = useRoute();
  // On a phone the conversation is a page of its own, and the one it opens on; on a desktop it's always beside.
  const pages: Page[] = [...(narrow ? ["chat" as Page] : []), "tasks", "cockpit", "prompts", "connectors"];
  const home = narrow ? "chat" : "tasks";
  const shown = route.page && pages.includes(route.page) ? route.page : home;
  useEffect(() => {
    if (shown !== route.page) history.replaceState(null, "", `/${shown}`);
  }, [shown, route.page]);

  return (
    <div className="app">
      <header>
        <Link to={`/${home}`} className="brand">
          Archangel
        </Link>
        <nav>
          {pages.map((name) => (
            <Link key={name} to={`/${name}`} className={shown === name ? "active" : ""}>
              {name[0].toUpperCase() + name.slice(1)}
            </Link>
          ))}
        </nav>
        {PUBLIC && (
          <a className="sign-out" href="/auth/logout">
            Sign out
          </a>
        )}
      </header>
      <main className={shown}>
        {shown !== "chat" &&
          {
            tasks: <Tasks selected={route.id} select={(id) => go(id ? `/tasks/${id}` : "/tasks")} />,
            cockpit: <Cockpit />,
            prompts: <Prompts />,
            connectors: <Connectors />,
          }[shown]}
        <Chat visible={!narrow || shown === "chat"} />
      </main>
    </div>
  );
}
