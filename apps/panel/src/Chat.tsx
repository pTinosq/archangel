import { useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl } from "./api";
import { useFeed } from "./poll";
import { play } from "./sounds";

/** `visible` false: kept mounted but hidden (a phone on another tab), so the draft survives. */
export function Chat({ visible }: { visible: boolean }) {
  const lines = useFeed(api.transcript);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [lines.length, visible]); // a hidden list loses its scroll: back to the end when shown
  const heard = useRef<number>(undefined);
  useEffect(() => {
    const last = lines.at(-1);
    // Not for the history on load: only a reply that arrives while the panel is open.
    if (heard.current !== undefined && last && last.id > heard.current && last.sender === "archangel") play("receive");
    if (lines.length) heard.current = last!.id;
  }, [lines.length]);

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    try {
      await api.say(text);
      play("send");
      setDraft("");
      setError("");
    } catch {
      play("error");
      setError("Couldn't reach Archangel. Is `just agent` running?");
    }
  };

  return (
    <section className="chat" hidden={!visible}>
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
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message Archangel"
          enterKeyHint="send"
        />
        <button type="submit" data-sound="">
          Send
        </button>
      </form>
    </section>
  );
}
