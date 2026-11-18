import asyncio
import io
import json
import re
import zipfile

import httpx

from agent.coder import Coder

NOW = "2099-01-01T00:00:00Z"  # after any test's start


class FakeGitHub:
    """GitHub's API for improve runs: each dispatch starts the next of `runs` (its conclusion and its `result`
    artifact, if any), running once, then finished. Run ids start at 7."""

    def __init__(self, result: dict | None, conclusion: str = "success", then: list[tuple[str, dict | None]] = ()):
        self.runs = [(conclusion, result), *then]
        self.started = 0  # runs dispatched (one may already be running when Archangel restarts)
        self.dispatched, self.merged, self.looks = None, None, {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, repo = request.url.path, "/repos/pTinosq/archangel"
        if path == f"{repo}/actions/workflows/improve.yml/dispatches":
            self.dispatched = json.loads(request.content)
            self.started += 1
            return httpx.Response(204)
        if path == f"{repo}/actions/workflows/improve.yml/runs":
            others = {"id": 6, "display_title": "archangel/task-9", "created_at": NOW}
            return httpx.Response(
                200, json={"workflow_runs": [others, *(self._run(7 + i) for i in range(self.started))]}
            )
        if match := re.fullmatch(rf"{repo}/actions/runs/(\d+)", path):
            id = int(match[1])
            self.looks[id] = self.looks.get(id, 0) + 1
            return httpx.Response(200, json=self._run(id))
        if match := re.fullmatch(rf"{repo}/actions/runs/(\d+)/artifacts", path):
            result = self.runs[int(match[1]) - 7][1]
            url = f"https://api.github.com/zip/{match[1]}"
            return httpx.Response(
                200, json={"artifacts": [{"name": "result", "archive_download_url": url}] if result else []}
            )
        if match := re.fullmatch(r"/zip/(\d+)", path):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("result.json", json.dumps(self.runs[int(match[1]) - 7][1]))
            return httpx.Response(200, content=archive.getvalue())
        if path == f"{repo}/pulls/12" and request.method == "GET":
            return httpx.Response(
                200, json={"state": "open", "title": "Add Telegram", "head": {"ref": "archangel/task-3"}}
            )
        if path == f"{repo}/pulls/12/merge":
            self.merged = json.loads(request.content)
            return httpx.Response(200, json={"merged": True})
        return httpx.Response(404, json={"message": "Not Found"})

    def _run(self, id: int) -> dict:
        done = self.looks.get(id, 0) >= 2
        return {
            "id": id,
            "display_title": "archangel/task-1",
            "created_at": NOW,
            "html_url": f"https://github.com/pTinosq/archangel/actions/runs/{id}",
            "status": "completed" if done else "in_progress",
            "conclusion": self.runs[id - 7][0] if done else None,
        }


def coder(github: FakeGitHub) -> Coder:
    client = httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(github))
    return Coder(client, poll=0, retry=0)


def change(github: FakeGitHub) -> tuple[bool, str, list[str]]:
    steps = []
    worked, report = asyncio.run(coder(github).change("Add a Telegram channel", "archangel/task-1", steps.append))
    return worked, report, steps


def test_a_change_comes_back_as_a_pr():
    pr = "https://github.com/pTinosq/archangel/pull/12"
    github = FakeGitHub({"title": "Add Telegram", "summary": "Set TELEGRAM_TOKEN.", "pr": pr, "tests": "passed"})
    worked, report, steps = change(github)
    assert github.dispatched == {
        "ref": "main",
        "inputs": {"request": "Add a Telegram channel", "branch": "archangel/task-1"},
    }
    assert steps == ["Claude Code is on it: https://github.com/pTinosq/archangel/actions/runs/7"]  # its own run
    assert (worked, report) == (True, f"{pr} (tests pass)\nAdd Telegram\nSet TELEGRAM_TOKEN.")


def test_failing_tests_are_said_plainly():
    github = FakeGitHub({"title": "T", "summary": "S", "pr": "https://x/pull/1", "tests": "failed"})
    assert "TESTS FAIL" in change(github)[1]


def test_a_question_comes_back_answered():
    github = FakeGitHub({"title": "", "summary": "Notion's sign-in expired.", "pr": "", "tests": ""})
    assert change(github)[:2] == (True, "No change made. Notion's sign-in expired.")


def test_a_failed_run_links_to_it():
    worked, report, _ = change(FakeGitHub(None, conclusion="failure"))
    assert (worked, report) == (False, "The run failure: https://github.com/pTinosq/archangel/actions/runs/7")


def test_a_failed_run_says_why():
    github = FakeGitHub({"error": "Tests timed out.", "turns": 31}, conclusion="failure")
    worked, report, steps = change(github)
    assert (worked, report) == (
        False,
        "The run failed: Tests timed out.\nhttps://github.com/pTinosq/archangel/actions/runs/7",
    )
    assert github.started == 1  # it got going, so trying again would only fail the same way


def test_a_run_claude_never_started_in_is_tried_once_more():
    limit = {"error": "Usage limit reached.", "turns": 0}
    pr = {"title": "T", "summary": "S", "pr": "https://x/pull/1", "tests": "passed"}
    worked, report, steps = change(FakeGitHub(limit, conclusion="failure", then=[("success", pr)]))
    assert (worked, report) == (True, "https://x/pull/1 (tests pass)\nT\nS")
    assert steps == [
        "Claude Code is on it: https://github.com/pTinosq/archangel/actions/runs/7",
        "Claude Code didn't start (Usage limit reached.); trying again in a minute.",
        "Claude Code is on it: https://github.com/pTinosq/archangel/actions/runs/8",  # a new run, not the failed one
    ]
    github = FakeGitHub(limit, conclusion="failure", then=[("failure", limit)])
    assert change(github)[:2] == (
        False,
        "The run failed: Usage limit reached.\nhttps://github.com/pTinosq/archangel/actions/runs/8",
    )
    assert github.started == 2  # only once more


def test_a_run_already_going_is_followed_not_started_again():
    """Archangel restarted (often for its own merge) while the run went on."""
    pr = {"title": "T", "summary": "S", "pr": "https://x/pull/1", "tests": "passed"}
    github = FakeGitHub(pr)
    github.started = 1
    found, steps = [], []
    worked, report = asyncio.run(coder(github).change("Add a Telegram channel", "", steps.append, found.append, run=7))
    assert (worked, report, github.dispatched, found) == (True, "https://x/pull/1 (tests pass)\nT\nS", None, [])


def test_each_run_is_told_as_it_starts():
    found = []
    asyncio.run(
        coder(FakeGitHub({"title": "", "summary": "S", "pr": "", "tests": ""})).change(
            "Q", "archangel/task-1", lambda _: None, found.append
        )
    )
    assert found == [7]


def test_a_pr_is_carried_on_and_merged():
    github = FakeGitHub(None)
    assert asyncio.run(coder(github).branch(12)) == "archangel/task-3"
    assert asyncio.run(coder(github).merge(12)) == "Merged PR #12 (Add Telegram); it deploys in a few minutes."
    assert github.merged == {"merge_method": "squash"}
