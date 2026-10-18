import { useState } from "react";
import { Chat } from "./Chat";
import { Cockpit } from "./Cockpit";
import { useNarrow } from "./narrow";
import { Prompts } from "./Prompts";
import { Tasks } from "./Tasks";

type Tab = "chat" | "tasks" | "cockpit" | "prompts";
/** Served by the agent on the internet, behind GitHub sign-in; locally there's no one to sign out. */
const PUBLIC = !["localhost", "127.0.0.1"].includes(location.hostname);

export function App() {
  const narrow = useNarrow();
  // On a phone the conversation is a tab of its own, and the one it opens on; on a desktop it's always beside.
  const [tab, setTab] = useState<Tab>(narrow ? "chat" : "tasks");
  const tabs: Tab[] = narrow ? ["chat", "tasks", "cockpit", "prompts"] : ["tasks", "cockpit", "prompts"];
  const shown = tabs.includes(tab) ? tab : "tasks";
  return (
    <div className="app">
      <header>
        <h1>Archangel</h1>
        <nav>
          {tabs.map((name) => (
            <button key={name} className={shown === name ? "active" : ""} onClick={() => setTab(name)}>
              {name[0].toUpperCase() + name.slice(1)}
            </button>
          ))}
        </nav>
        {PUBLIC && (
          <a className="sign-out" href="/auth/logout">
            Sign out
          </a>
        )}
      </header>
      <main className={shown}>
        {shown !== "chat" && { tasks: <Tasks />, cockpit: <Cockpit />, prompts: <Prompts /> }[shown]}
        <Chat visible={!narrow || shown === "chat"} />
      </main>
    </div>
  );
}
