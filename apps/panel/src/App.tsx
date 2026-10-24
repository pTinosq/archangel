import { useEffect, useRef, useSyncExternalStore } from "react";
import { api } from "./api";
import { Chat } from "./Chat";
import { Cockpit } from "./Cockpit";
import { Connectors } from "./Connectors";
import { useNarrow } from "./narrow";
import { usePoll } from "./poll";
import { Prompts } from "./Prompts";
import { play, setSoundOn, soundOn, subscribeSound } from "./sounds";
import { go, Link, type Page, useRoute } from "./route";
import { Tasks } from "./Tasks";
import { Twin } from "./Twin";

/** Served by the agent on the internet, behind GitHub sign-in; locally there's no one to sign out. */
const PUBLIC = !["localhost", "127.0.0.1"].includes(location.hostname);

export function App() {
  const narrow = useNarrow();
  const route = useRoute();
  // On a phone the conversation is a page of its own, and the one it opens on; on a desktop it's always beside.
  const pages: Page[] = [...(narrow ? ["chat" as Page] : []), "tasks", "cockpit", "twin", "prompts", "connectors"];
  const home = narrow ? "chat" : "tasks";
  const shown = route.page && pages.includes(route.page) ? route.page : home;
  useEffect(() => {
    if (shown !== route.page) history.replaceState(null, "", `/${shown}`);
  }, [shown, route.page]);

  useTaskSounds();

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
        <SoundSwitch />
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
            twin: <Twin />,
            prompts: <Prompts />,
            connectors: <Connectors />,
          }[shown]}
        <Chat visible={!narrow || shown === "chat"} />
      </main>
    </div>
  );
}

function SoundSwitch() {
  const on = useSyncExternalStore(subscribeSound, soundOn);
  return (
    <button
      className="sound-switch"
      data-sound=""
      aria-pressed={on}
      onClick={() => {
        // Heard either way: the switch going off, then silence; or the sound coming back on.
        if (on) play("toggleOff").then(() => setSoundOn(false));
        else (setSoundOn(true), play("toggleOn"));
      }}
    >
      Sound {on ? "on" : "off"}
    </button>
  );
}

/** A ding when a task starts waiting on the owner, a softer one when one finishes; nothing for what was already so. */
function useTaskSounds() {
  const tasks = usePoll(api.tasks);
  const seen = useRef<Map<number, string>>(undefined);
  useEffect(() => {
    if (!tasks) return;
    const before = seen.current;
    seen.current = new Map(tasks.map((t) => [t.id, t.status]));
    if (!before) return;
    const changed = tasks.filter((t) => before.get(t.id) !== t.status);
    if (changed.some((t) => t.status.startsWith("needs"))) play("needs");
    else if (changed.some((t) => t.status === "done")) play("done");
  }, [tasks]);
}
