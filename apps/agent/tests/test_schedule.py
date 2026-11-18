import asyncio
from dataclasses import replace
from datetime import datetime

import pytest

from agent.events import Scheduled
from agent.messages import Address
from agent.schedule import DEFAULT_RUNS, MAX_RUNS, TIMEZONE, Schedule, following, local, parse, when

SMS = Address("sms", "owner")
NOW = datetime(2026, 11, 17, 20, 10, tzinfo=TIMEZONE).timestamp()


def test_times_are_the_owner_s_local_time_or_minutes_from_now():
    assert parse(None, 15, None, NOW) == NOW + 15 * 60
    assert parse("2026-11-18 08:00", None, None, NOW) == datetime(2026, 11, 18, 8, tzinfo=TIMEZONE).timestamp()
    summer = datetime.fromisoformat("2027-07-01 08:00+01:00").timestamp()
    assert parse("2027-07-01 08:00", None, None, NOW) == summer  # British Summer Time, not GMT


@pytest.mark.parametrize(
    ("at", "in_minutes", "said"),
    [
        (None, None, "Give either"),
        ("2026-11-18 08:00", 5, "Give either"),
        ("8am", None, "Couldn't read '8am'"),
        ("2026-11-17 19:00", None, "That's past: it's Tue 17 Nov 20:10 now."),
    ],
)
def test_a_time_must_be_one_readable_future_moment(at, in_minutes, said):
    with pytest.raises(ValueError, match=said):
        parse(at, in_minutes, None, NOW)


def test_times_are_shown_both_ways():
    assert when(NOW + 15 * 60, NOW) == "Tue 17 Nov 20:25, in 15 min"
    assert when(parse("2026-11-18 08:00", None, None, NOW), NOW) == "Wed 18 Nov 08:00, in 11 h 50 min"
    assert when(NOW + 5 * 24 * 3600, NOW) == "Sun 22 Nov 20:10, in 5 days"


def test_what_s_scheduled_survives_a_restart_and_cancelling(store):
    schedule = Schedule(store.db, lambda: NOW)
    later = schedule.add(NOW + 3600, "Ring them with the weather", "call me at 9 with the weather", SMS, 4)
    sooner = schedule.add(NOW + 60, "Text them: call Sam", "", SMS, None)
    assert Schedule(store.db, lambda: NOW).upcoming() == [sooner, later]
    assert schedule.describe() == (
        "#2 Tue 17 Nov 20:11, in 1 min (via sms): Text them: call Sam\n"
        "#1 Tue 17 Nov 21:10, in 1 h (via sms): Ring them with the weather"
    )
    assert schedule.cancel(2) and not schedule.cancel(2)
    assert schedule.upcoming() == [later]


def test_due_jobs_fire_once_in_order_and_later_ones_wait(store):
    clock = [NOW]
    schedule = Schedule(store.db, lambda: clock[0])
    fired = []

    async def fire(job):
        fired.append(job.what)

    async def scenario():
        schedule.add(NOW - 600, "missed while down", "", SMS, None)
        schedule.add(NOW - 60, "just due", "", SMS, None)
        schedule.add(NOW + 600, "later", "", SMS, None)
        watching = asyncio.create_task(schedule.watch(fire))
        await asyncio.sleep(0.05)
        assert fired == ["missed while down", "just due"]
        clock[0] = NOW + 601
        schedule.add(NOW + 9999, "much later", "", SMS, None)  # a change wakes it, and it looks at the clock again
        await asyncio.sleep(0.05)
        watching.cancel()

    asyncio.run(scenario())
    assert fired == ["missed while down", "just due", "later"]
    assert [job.what for job in Schedule(store.db).upcoming()] == ["much later"]


def test_a_job_due_long_ago_says_so(store):
    job = Schedule(store.db, lambda: NOW - 3600).add(NOW - 3000, "Text them: call Sam", "text me at 8", SMS, 2)
    prompt = Scheduled(job).prompt()[0]
    assert "but Archangel was down" in prompt and "Do it only if it still makes sense" in prompt
    assert Scheduled(job).thread == 2 and Scheduled(job).reply_to == SMS and not Scheduled(job).from_owner


def test_repeating_starts_at_its_next_time_or_when_asked():
    weekdays_at_7 = "0 7 * * 1-5"
    assert local(parse(None, None, weekdays_at_7, NOW)) == "Wed 18 Nov 07:00"
    assert local(parse("2026-11-23 09:00", None, weekdays_at_7, NOW)) == "Mon 23 Nov 09:00"  # starts Monday
    with pytest.raises(ValueError, match="Couldn't read 'weekdays' as cron"):
        parse(None, None, "weekdays", NOW)
    friday = datetime(2026, 11, 20, 7, tzinfo=TIMEZONE).timestamp()
    assert local(following(weekdays_at_7, friday)) == "Mon 23 Nov 07:00"
    summer = datetime(2027, 3, 27, 7, tzinfo=TIMEZONE).timestamp()  # the clocks go forward that night
    assert local(following("0 7 * * *", summer)) == "Sun 28 Mar 07:00"


def test_a_repeating_job_fires_once_however_long_it_was_missed_then_moves_on(store):
    schedule = Schedule(store.db, lambda: NOW)
    fired = []

    async def fire(job):
        fired.append(job.id)

    async def scenario():
        schedule.add(NOW - 3 * 24 * 3600, "Ring them with the weather", "", SMS, None, "0 7 * * *")
        watching = asyncio.create_task(schedule.watch(fire))
        await asyncio.sleep(0.05)
        watching.cancel()

    asyncio.run(scenario())
    assert fired == [1]
    [job] = schedule.upcoming()
    assert local(job.due) == "Wed 18 Nov 07:00" and job.every == "0 7 * * *"
    assert (
        schedule.describe()
        == "#1 Wed 18 Nov 07:00, in 10 h 50 min, repeats 0 7 * * * (run 2 of 50) (via sms): Ring them with the weather"
    )
    assert schedule.cancel(1) and not schedule.upcoming()


def test_a_repeating_job_says_when_it_comes_round_next(store):
    job = Schedule(store.db, lambda: NOW).add(NOW, "Ring them with the weather", "", SMS, None, "0 7 * * 1-5")
    prompt = Scheduled(replace(job, runs=1)).prompt()[0]
    assert "it repeats (0 7 * * 1-5), run 1 of 50, next " in prompt
    assert "cancel_later(1)" in prompt  # how the loop ends itself, once what it waited for has happened


def fire_all(schedule, clock, days):
    """Lets the schedule run for `days` days, a day at a time; the jobs it fired, as fired."""
    runs = []

    async def fire(job):
        runs.append(job)

    async def scenario():
        watching = asyncio.create_task(schedule.watch(fire))
        for _ in range(days):
            await asyncio.sleep(0.03)
            clock[0] += 24 * 3600
            schedule._changed.set()
        await asyncio.sleep(0.03)
        watching.cancel()

    asyncio.run(scenario())
    return runs


def test_a_repeat_stops_at_its_cap_and_says_the_last_run_was_the_last(store):
    clock = [NOW]
    schedule = Schedule(store.db, lambda: clock[0])
    schedule.add(NOW - 1, "Check the email", "", SMS, None, "0 7 * * *", max_runs=3)
    runs = fire_all(schedule, clock, 6)
    assert [job.runs for job in runs] == [1, 2, 3]  # never a fourth
    assert [job.last for job in runs] == [False, False, True]
    assert not schedule.upcoming() and schedule.get(1) is None
    assert "this was its last run, 3 of 3: it has stopped, so tell the owner" in Scheduled(runs[-1]).prompt()[0]
    assert "cancel_later" in Scheduled(runs[0]).prompt()[0]


def test_a_repeat_s_cap_has_a_floor_and_a_ceiling_and_a_one_off_has_none(store):
    schedule = Schedule(store.db, lambda: NOW)
    assert schedule.add(NOW, "a", "", SMS, None, "0 7 * * *", max_runs=0).max_runs == 1
    assert schedule.add(NOW, "b", "", SMS, None, "0 7 * * *", max_runs=10_000).max_runs == MAX_RUNS
    assert schedule.add(NOW, "c", "", SMS, None, "0 7 * * *").max_runs == DEFAULT_RUNS
    one_off = schedule.add(NOW, "d", "", SMS, None)
    assert one_off.max_runs == 0 and not one_off.last


def test_cancelling_a_repeat_ends_all_its_runs_and_a_one_off_already_run_cannot_be_cancelled(store):
    clock = [NOW]
    schedule = Schedule(store.db, lambda: clock[0])
    schedule.add(NOW - 1, "Check the email", "", SMS, None, "0 7 * * *")
    assert len(fire_all(schedule, clock, 1)) >= 1
    assert schedule.get(1) and schedule.cancel(1)  # still repeating, so it can be stopped
    assert fire_all(schedule, clock, 3) == [] and not schedule.upcoming()
    done = schedule.add(clock[0] - 1, "Text them", "", SMS, None)
    assert len(fire_all(schedule, clock, 0)) == 1
    assert schedule.get(done.id) is None and not schedule.cancel(done.id)


def test_an_old_schedule_table_gains_the_run_columns(store):
    store.db.execute("DROP TABLE IF EXISTS schedule")
    store.db.execute(
        "CREATE TABLE schedule (id INTEGER PRIMARY KEY, due REAL, what TEXT, said TEXT, channel TEXT, address TEXT,"
        " thread INTEGER, created REAL, fired REAL, every TEXT NOT NULL DEFAULT '')"
    )
    store.db.execute("INSERT INTO schedule (due, what, said, channel, address, created) VALUES (1,'x','','sms','o',0)")
    [job] = Schedule(store.db).upcoming()
    assert (job.runs, job.max_runs) == (0, 0)
