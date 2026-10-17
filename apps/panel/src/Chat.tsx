import { useEffect, useRef, useState } from "react";
import { api, lineImageUrl } from "./api";
import { useFeed } from "./poll";

export function Chat() {
  const lines = useFeed(api.chat);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [lines.length]);

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      await api.say(text);
      setDraft("");
      setError("");
    } catch {
      setError("Couldn't reach Archangel. Is `just agent` running?");
    }
  };

  return (
    <section className="chat">
      <h2>Chat</h2>
      <div className="lines">
        {lines.map((line) => (
          <div key={line.id} className={`line ${line.sender}`}>
            <p>{line.text}</p>
            {line.image && (
              <a href={lineImageUrl(line.id)} target="_blank">
                <img src={lineImageUrl(line.id)} alt="" />
              </a>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
      {error && <p className="error">{error}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Message Archangel" />
        <button type="submit">Send</button>
      </form>
    </section>
  );
}
