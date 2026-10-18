import { useEffect, useState } from "react";
import { api, type Connector, type Service } from "./api";

const LABELS: Record<string, string> = { client_id: "Client ID", client_secret: "Client secret", url: "Address" };

export function Connectors() {
  const [services, setServices] = useState<Service[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [adding, setAdding] = useState<Service | null>(null);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [profile, setProfile] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false); // reaching the service takes a few seconds

  const load = () =>
    api.connectors().then((found) => {
      setServices(found.services);
      setConnectors(found.connectors);
    });
  useEffect(() => {
    load();
  }, []);

  // Signing in happens on the service's own site, which sends the owner back to Archangel.
  const signIn = (starting: Promise<{ signIn: string | null }>) => {
    setError("");
    setBusy(true);
    starting
      .then(({ signIn }) => (signIn ? location.assign(signIn) : load().then(() => setBusy(false))))
      .catch((e: Error) => {
        setError(e.message);
        setBusy(false);
      });
  };
  const profiles = [...new Set(connectors.map((c) => c.profile))].sort();
  const choose = (service: Service) => {
    setAdding(service);
    setSettings({});
    setError("");
  };

  return (
    <>
      <section className="connector-list">
        <h2>Connectors</h2>
        {connectors.length === 0 && (
          <p className="quiet">None yet. Archangel reaches what's connected without the phone.</p>
        )}
        {profiles.map((p) => (
          <div key={p}>
            {(profiles.length > 1 || p) && <h3>{p || "No profile"}</h3>}
            <ul>
              {connectors
                .filter((c) => c.profile === p)
                .map((c) => (
                  <li key={c.name}>
                    <div>
                      <strong>{c.title}</strong> {c.account && <span className="quiet">{c.account}</span>}
                    </div>
                    <span className={`status ${c.signedIn ? "done" : "needs-answer"}`}>
                      {c.signedIn ? "connected" : "needs sign-in"}
                    </span>
                    <div className="actions">
                      <button
                        className={c.signedIn ? "" : "approve"}
                        disabled={busy}
                        onClick={() => signIn(api.signIn(c.name))}
                      >
                        {c.signedIn ? "Reconnect" : "Sign in"}
                      </button>
                      <button onClick={() => api.removeConnector(c.name).then(load)}>Remove</button>
                    </div>
                  </li>
                ))}
            </ul>
          </div>
        ))}
        <p className="quiet">Reading is free; anything that sends, writes or deletes asks you first.</p>
      </section>
      <section className="add-connector">
        <h2>Add</h2>
        <div className="services">
          {services.map((s) => (
            <button key={s.kind} className={adding?.kind === s.kind ? "selected" : ""} onClick={() => choose(s)}>
              {s.title}
            </button>
          ))}
        </div>
        {adding && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              signIn(api.addConnector(adding.kind, settings, profile));
            }}
          >
            <label>
              Profile, e.g. work or personal
              <input value={profile} onChange={(e) => setProfile(e.target.value)} list="profiles" autoComplete="off" />
              <datalist id="profiles">
                {profiles.filter(Boolean).map((p) => (
                  <option key={p} value={p} />
                ))}
              </datalist>
            </label>
            {adding.help && <p className="quiet help">{adding.help}</p>}
            {adding.needs.map((need) => (
              <label key={need}>
                {LABELS[need] ?? need}
                <input
                  type={need.includes("secret") ? "password" : "text"}
                  value={settings[need] ?? ""}
                  onChange={(e) => setSettings({ ...settings, [need]: e.target.value })}
                  autoComplete="off"
                />
              </label>
            ))}
            <button className="approve" disabled={busy}>
              {busy ? "Connecting…" : `Connect ${adding.title}`}
            </button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
      </section>
    </>
  );
}
