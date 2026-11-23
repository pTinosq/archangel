import { useEffect, useState } from "react";
import { api, type QuickTool } from "./api";

/** The quick tools: live data Archangel answers from in seconds, without a device. Switched on and off here, with
 * what each needs (TfL's optional app key). */
export function QuickTools() {
  const [tools, setTools] = useState<QuickTool[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
  const [error, setError] = useState("");

  const change = (tool: QuickTool, body: { enabled?: boolean; settings?: Record<string, string | string[]> }) => {
    setError("");
    api
      .setTool(tool.name, body)
      .then((found) => {
        setTools(found);
        if (body.settings) setDrafts({ ...drafts, [tool.name]: {} });
      })
      .catch((e: Error) => setError(e.message));
  };
  useEffect(() => {
    api.tools().then(setTools);
  }, []);

  return (
    <section className="quick-tools">
      <h2>Quick tools</h2>
      <p className="quiet">
        Live answers in seconds, without the phone or the web. Archangel tries these first.
      </p>
      <ul>
        {tools.map((tool) => {
          const draft = drafts[tool.name] ?? {};
          // A secret left blank is unchanged; anything else is sent, an emptied field clearing it.
          const single = tool.settings.filter((s) => !s.items);
          const edits = single.filter((s) => (s.secret ? !!draft[s.key] : (draft[s.key] ?? s.value) !== s.value));
          const missing = tool.settings.some((s) => s.required && !s.set);
          return (
            <li key={tool.name}>
              <div>
                <strong>{tool.title}</strong> <span className="quiet">{tool.about}</span>
              </div>
              <span className={`status ${tool.active ? "done" : missing && tool.enabled ? "needs-answer" : ""}`}>
                {tool.active ? "on" : missing && tool.enabled ? "needs setup" : "off"}
              </span>
              {tool.settings
                .filter((s) => s.items)
                .map((s) => (
                  <div key={s.key} className="items">
                    <span>{s.label}</span>
                    <ul>
                      {s.list.map((item) => (
                        <li key={item}>
                          {item}{" "}
                          <button
                            onClick={() => change(tool, { settings: { [s.key]: s.list.filter((i) => i !== item) } })}
                          >
                            Remove
                          </button>
                        </li>
                      ))}
                    </ul>
                    <input
                      type="text"
                      placeholder="Add an address, press Enter"
                      value={draft[s.key] ?? ""}
                      onChange={(e) => setDrafts({ ...drafts, [tool.name]: { ...draft, [s.key]: e.target.value } })}
                      onKeyDown={(e) => {
                        const added = (draft[s.key] ?? "").trim();
                        if (e.key === "Enter" && added) change(tool, { settings: { [s.key]: [...s.list, added] } });
                      }}
                      autoComplete="off"
                    />
                  </div>
                ))}
              {single.map((s) => (
                <label key={s.key}>
                  {s.label}
                  <input
                    type={s.secret ? "password" : "text"}
                    value={draft[s.key] ?? s.value}
                    placeholder={s.secret && s.set ? "saved: type to replace" : ""}
                    onChange={(e) => setDrafts({ ...drafts, [tool.name]: { ...draft, [s.key]: e.target.value } })}
                    autoComplete="off"
                  />
                </label>
              ))}
              <div className="actions">
                <button
                  className={tool.enabled ? "" : "approve"}
                  data-sound={tool.enabled ? "toggleOff" : "toggleOn"}
                  onClick={() => change(tool, { enabled: !tool.enabled })}
                >
                  {tool.enabled ? "Turn off" : "Turn on"}
                </button>
                {edits.length > 0 && (
                  <button
                    className="approve"
                    onClick={() =>
                      change(tool, { settings: Object.fromEntries(edits.map((s) => [s.key, draft[s.key] ?? ""])) })
                    }
                  >
                    Save
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
