import { useState } from "react";
import { Chat } from "./Chat";
import { Prompts } from "./Prompts";
import { Tasks } from "./Tasks";

type Tab = "tasks" | "prompts";

export function App() {
  const [tab, setTab] = useState<Tab>("tasks");
  return (
    <div className="app">
      <header>
        <h1>Archangel</h1>
        <nav>
          {(["tasks", "prompts"] as const).map((name) => (
            <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
              {name[0].toUpperCase() + name.slice(1)}
            </button>
          ))}
        </nav>
      </header>
      <main className={tab}>
        {tab === "tasks" ? <Tasks /> : <Prompts />}
        <Chat />
      </main>
    </div>
  );
}
