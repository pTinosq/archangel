import { useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl, type Step, type Task } from "./api";
import { useNarrow } from "./narrow";
import { useFeed, usePoll } from "./poll";

/** `selected`: the task in the address, /tasks/12. */
export function Tasks({ selected, select }: { selected?: number; select: (id?: number) => void }) {
  const tasks = usePoll(api.tasks);
  const narrow = useNarrow();
  // A desktop always shows one beside the list; a phone shows the list, or the one picked from it.
  const shown = tasks?.find((t) => t.id === selected) ?? (narrow ? undefined : tasks?.[0]);
  const back = narrow ? () => select() : undefined;

  return (
    <>
      {!(narrow && shown) && (
        <section className="task-list">
          <h2>Tasks</h2>
          {tasks?.length === 0 && <p className="quiet">None yet. Ask Archangel to do something on the phone, or to research something.</p>}
          <ul>
            {tasks?.map((task) => (
              <li key={task.id} className={task.id === shown?.id ? "selected" : ""} onClick={() => select(task.id)}>
                <div>
                  <span className="id">#{task.id}</span> <Status task={task} />
                </div>
                <div className="goal">{task.goal}</div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {shown ? <TaskDetail key={shown.id} task={shown} back={back} /> : !narrow && <section className="detail" />}
    </>
  );
}

function Status({ task }: { task: Task }) {
  return <span className={`status ${task.status.replace(" ", "-")}`}>{task.status}</span>;
}

/** `back` (on a phone): returns to the list. */
function TaskDetail({ task, back }: { task: Task; back?: () => void }) {
  const steps = useFeed((after) => api.steps(task.id, after), task.id);
  const [viewing, setViewing] = useState<Step>();
  const shownScreen = viewing ?? steps.findLast((s) => s.screen);
  const end = useRef<HTMLLIElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // On a phone the page is one column: following the newest step would scroll past the approve buttons.
    if (!back) end.current?.scrollIntoView({ block: "nearest" });
  }, [steps.length]);
  useEffect(() => {
    // On a phone the screen is above the steps, out of sight: bring it into view when a step is picked.
    if (viewing) screen.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [viewing]);

  return (
    <section className="detail">
      <div className="head">
        {back && (
          <button className="back" onClick={back}>
            ‹ Tasks
          </button>
        )}
        <h2>
          #{task.id} {task.goal}
        </h2>
        <p>
          <Status task={task} /> <span className="quiet">reports via {task.reportTo}</span>
        </p>
        {task.page && <Report task={task} />}
        {task.report && <p className="report">{task.report}</p>}
        {task.open && <Controls task={task} />}
      </div>
      <div className="screen" ref={screen}>
        {shownScreen ? (
          <>
            <img src={imageUrl(shownScreen.screen!)} alt="Phone screen" onError={hideExpired} />
            <p className="quiet">
              {viewing ? (
                <>
                  Step at {clock(viewing.time)} · <a onClick={() => setViewing(undefined)}>show latest</a>
                </>
              ) : (
                `Latest screen, ${clock(shownScreen.time)}`
              )}
            </p>
          </>
        ) : (
          <p className="quiet">{task.kind !== "phone" ? "Research is on the web, not the phone." : "No screens yet."}</p>
        )}
      </div>
      <ol className="steps">
        {steps.map((step) => (
          <li
            key={step.id}
            ref={step === steps[steps.length - 1] ? end : undefined}
            className={[step.screen && "has-screen", step === viewing && "viewing"].filter(Boolean).join(" ")}
            onClick={() => step.screen && setViewing(step)}
          >
            <time>{clock(step.time)}</time>
            <span>{step.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The report's link, and who it opens for: private (signed in) by default, public to share it. */
function Report({ task }: { task: Task }) {
  const [isPublic, setPublic] = useState(task.public);
  useEffect(() => setPublic(task.public), [task.public]);
  const flip = () => api.makePublic(task.id, !isPublic).then((made) => setPublic(made.public));

  return (
    <p>
      <a href={`/reports/${task.id}`} target="_blank" rel="noreferrer">
        Open the report
      </a>
      <span className="quiet">{isPublic ? "· public: anyone with the link" : "· private: only you"}</span>
      <button onClick={flip}>{isPublic ? "Make private" : "Make public"}</button>
    </p>
  );
}

/** Shortcuts for messages the owner could type: they go through the conversation like any other. */
function Controls({ task }: { task: Task }) {
  const [reply, setReply] = useState("");
  const [sent, setSent] = useState("");
  const say = (text: string) => api.say(text).then(() => setSent(text));
  const answer = () => reply && say(`Task #${task.id}: ${reply}`).then(() => setReply(""));

  return (
    <div className={task.status.startsWith("needs") ? "controls waiting" : "controls"}>
      {task.status === "needs approval" && (
        <>
          <button className="approve" onClick={() => say(`Yes, approve task #${task.id}.`)}>Approve</button>
          <button onClick={() => say(`No, don't do it (task #${task.id}).`)}>Decline</button>
        </>
      )}
      {task.status === "needs answer" && (
        <>
          <input
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && answer()}
            placeholder="Your answer"
            enterKeyHint="send"
          />
          <button onClick={answer} disabled={!reply}>Reply</button>
        </>
      )}
      <button onClick={() => say(`Cancel task #${task.id}.`)}>Cancel task</button>
      {sent && <p className="quiet">Sent “{sent}”</p>}
    </div>
  );
}

const clock = (time: number) => new Date(time * 1000).toLocaleTimeString();
