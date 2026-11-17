import asyncio
import io
import json
import zipfile

import httpx

from agent.coder import Coder

NOW = "2099-01-01T00:00:00Z"  # after any test's start


class FakeGitHub:
    """GitHub's API for one improve run: started, running once, then finished with `result` as its artifact."""

    def __init__(self, result: dict | None, conclusion: str = "success"):
        self.result, self.conclusion = result, conclusion
        self.dispatched, self.merged, self.looks = None, None, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, repo = request.url.path, "/repos/pTinosq/archangel"
        if path == f"{repo}/actions/workflows/improve.yml/dispatches":
            self.dispatched = json.loads(request.content)
            return httpx.Response(204)
        if path == f"{repo}/actions/workflows/improve.yml/runs":
            others = {"id": 6, "display_title": "archangel/task-9", "created_at": NOW}
            return httpx.Response(200, json={"workflow_runs": [others, self._run()]})
        if path == f"{repo}/actions/runs/7":
            self.looks += 1
            return httpx.Response(200, json=self._run())
        if path == f"{repo}/actions/runs/7/artifacts":
            artifacts = [{"name": "result", "archive_download_url": "https://api.github.com/zip/1"}] if self.result else []
            return httpx.Response(200, json={"artifacts": artifacts})
        if path == "/zip/1":
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("result.json", json.dumps(self.result))
            return httpx.Response(200, content=archive.getvalue())
        if path == f"{repo}/pulls/12" and request.method == "GET":
            return httpx.Response(200, json={"state": "open", "title": "Add Telegram", "head": {"ref": "archangel/task-3"}})
        if path == f"{repo}/pulls/12/merge":
            self.merged = json.loads(request.content)
            return httpx.Response(200, json={"merged": True})
        return httpx.Response(404, json={"message": "Not Found"})

    def _run(self) -> dict:
        done = self.looks >= 2
        return {
            "id": 7,
            "display_title": "archangel/task-1",
            "created_at": NOW,
            "html_url": "https://github.com/pTinosq/archangel/actions/runs/7",
            "status": "completed" if done else "in_progress",
            "conclusion": self.conclusion if done else None,
        }


def coder(github: FakeGitHub) -> Coder:
    return Coder(httpx.AsyncClient(base_url="https://api.github.com", transport=httpx.MockTransport(github)), poll=0)


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


def test_a_pr_is_carried_on_and_merged():
    github = FakeGitHub(None)
    assert asyncio.run(coder(github).branch(12)) == "archangel/task-3"
    assert asyncio.run(coder(github).merge(12)) == "Merged PR #12 (Add Telegram); it deploys in a few minutes."
    assert github.merged == {"merge_method": "squash"}
