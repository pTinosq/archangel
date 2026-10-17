import { useEffect, useRef, useState } from "react";
import { api, screenUrl, type Step, type Task } from "./api";
import { useFeed, usePoll } from "./poll";

export function Tasks() {
  const [tasks, refresh] = usePoll(api.tasks);
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
      {shown ? <TaskDetail key={shown.id} task={shown} changed={refresh} /> : <section className="detail" />}
    </>
  );
}

function Status({ task }: { task: Task }) {
  return <span className={`status ${task.status}`}>{task.status}</span>;
}

function TaskDetail({ task, changed }: { task: Task; changed: () => void }) {
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
            <img src={screenUrl(shownScreen.id)} alt="Phone screen" />
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
          <Status task={task} /> <span className="quiet">from {task.conversation}</span>
        </p>
        {task.report && <p className="report">{task.report}</p>}
        {task.status === "waiting" && <Waiting task={task} changed={changed} />}
        {task.open && task.status !== "waiting" && (
          <button onClick={() => api.cancel(task.id).then(changed)}>Cancel</button>
        )}
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

function Waiting({ task, changed }: { task: Task; changed: () => void }) {
  const [reply, setReply] = useState("");
  const approval = task.report.startsWith("Approve:");
  const send = (approve: boolean) => api.answer(task.id, reply, approve).then(changed);
  return (
    <div className="answer">
      <input
        value={reply}
        onChange={(e) => setReply(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !approval && reply && send(false)}
        placeholder={approval ? "Optional note" : "Your answer"}
      />
      {approval ? (
        <>
          <button className="approve" onClick={() => send(true)}>Approve</button>
          <button onClick={() => send(false)}>Decline</button>
        </>
      ) : (
        <button onClick={() => send(false)} disabled={!reply}>Reply</button>
      )}
      <button onClick={() => api.cancel(task.id).then(changed)}>Cancel task</button>
    </div>
  );
}

const clock = (time: number) => new Date(time * 1000).toLocaleTimeString();
