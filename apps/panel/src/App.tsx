import { useState } from "react";
import { Chat } from "./Chat";
import { Cockpit } from "./Cockpit";
import { Prompts } from "./Prompts";
import { Tasks } from "./Tasks";

type Tab = "tasks" | "cockpit" | "prompts";
/** Served by the agent on the internet, behind GitHub sign-in; locally there's no one to sign out. */
const PUBLIC = !["localhost", "127.0.0.1"].includes(location.hostname);

export function App() {
  const [tab, setTab] = useState<Tab>("tasks");
  return (
    <div className="app">
      <header>
        <h1>Archangel</h1>
        <nav>
          {(["tasks", "cockpit", "prompts"] as const).map((name) => (
            <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
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
      <main className={tab}>
        {{ tasks: <Tasks />, cockpit: <Cockpit />, prompts: <Prompts /> }[tab]}
        <Chat />
      </main>
    </div>
  );
}
