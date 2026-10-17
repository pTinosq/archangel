import { useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl } from "./api";
import { useFeed } from "./poll";

export function Chat() {
  const lines = useFeed(api.transcript);
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
      <h2>Conversation</h2>
      <div className="lines">
        {lines.map((line) => (
          <div key={line.id} className={`line ${line.sender}`}>
            <span className="via">{line.channel}</span>
            <p>{line.text}</p>
            {line.images.map((name) => (
              <a key={name} href={imageUrl(name)} target="_blank">
                <img src={imageUrl(name)} alt="" onError={hideExpired} />
              </a>
            ))}
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
