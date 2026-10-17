import { useEffect, useRef, useState } from "react";
import { api, hideExpired, imageUrl, type Step, type Task } from "./api";
import { useFeed, usePoll } from "./poll";

export function Tasks() {
  const tasks = usePoll(api.tasks);
  const [selected, setSelected] = useState<number>();
  const shown = tasks?.find((t) => t.id === selected) ?? tasks?.[0];

  return (
    <>
      <section className="task-list">
        <h2>Tasks</h2>
        {tasks?.length === 0 && <p className="quiet">None yet. Ask Archangel to do something on the phone.</p>}
        <ul>
          {tasks?.map((task) => (
            <li key={task.id} className={task.id === shown?.id ? "selected" : ""} onClick={() => setSelected(task.id)}>
              <div>
                <span className="id">#{task.id}</span> <Status task={task} />
              </div>
              <div className="goal">{task.goal}</div>
            </li>
          ))}
        </ul>
      </section>
      {shown ? <TaskDetail key={shown.id} task={shown} /> : <section className="detail" />}
    </>
  );
}

function Status({ task }: { task: Task }) {
  return <span className={`status ${task.status.replace(" ", "-")}`}>{task.status}</span>;
}

function TaskDetail({ task }: { task: Task }) {
  const steps = useFeed((after) => api.steps(task.id, after), task.id);
  const screens = steps.filter((s) => s.screen);
  const [viewing, setViewing] = useState<Step>();
  const latest = screens[screens.length - 1];
  const shownScreen = viewing ?? latest;
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [steps.length]);

  return (
    <section className="detail">
      <div className="screen">
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
          <p className="quiet">No screens yet.</p>
        )}
      </div>
      <div className="story">
        <h2>
          #{task.id} {task.goal}
        </h2>
        <p>
          <Status task={task} /> <span className="quiet">reports via {task.reportTo}</span>
        </p>
        {task.report && <p className="report">{task.report}</p>}
        {task.open && <Controls task={task} />}
        <ol className="steps">
          {steps.map((step) => (
            <li
              key={step.id}
              ref={step === steps[steps.length - 1] ? end : undefined}
              className={step.screen ? "has-screen" : ""}
              onClick={() => step.screen && setViewing(step)}
            >
              <time>{clock(step.time)}</time>
              <span>{step.text}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
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
