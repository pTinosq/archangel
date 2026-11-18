import { useEffect, useState } from "react";
import { api, type Prompt } from "./api";

const STYLE = "style "; // the writing styles' names among the prompts

export function Prompts() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selected, setSelected] = useState("orchestrator");
  const [draft, setDraft] = useState("");
  const prompt = prompts.find((p) => p.name === selected);

  useEffect(() => {
    api.prompts().then(setPrompts);
  }, []);
  useEffect(() => {
    setDraft(prompt?.text ?? "");
  }, [prompt]);

  const saved = (updated: Prompt) => setPrompts((all) => all.map((p) => (p.name === updated.name ? updated : p)));
  const unsaved = prompt !== undefined && draft !== prompt.text;
  const list = (shown: Prompt[]) => (
    <ul>
      {shown.map((p) => (
        <li key={p.name} className={p.name === selected ? "selected" : ""} onClick={() => setSelected(p.name)}>
          {p.name.replace(STYLE, "")} {p.edited && <span className="quiet">· edited</span>}
        </li>
      ))}
    </ul>
  );

  return (
    <>
      <section className="prompt-list">
        <h2>Prompts</h2>
        {list(prompts.filter((p) => !p.name.startsWith(STYLE)))}
        <h2>Writing styles</h2>
        {list(prompts.filter((p) => p.name.startsWith(STYLE)))}
        <p className="quiet">
          Edits apply from each agent's next run. Reset brings back the default: the code's, or a style's file.
        </p>
      </section>
      <section className="editor">
        {prompt && (
          <>
            {prompt.about && <p className="quiet">For {prompt.about}. Who a style is for is set in its file.</p>}
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
            <div className="actions">
              <button className="approve" disabled={!unsaved} onClick={() => api.editPrompt(prompt.name, draft).then(saved)}>
                Save
              </button>
              <button disabled={!unsaved} onClick={() => setDraft(prompt.text)}>
                Discard changes
              </button>
              <button disabled={!prompt.edited} onClick={() => api.resetPrompt(prompt.name).then(saved)}>
                Reset to default
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
