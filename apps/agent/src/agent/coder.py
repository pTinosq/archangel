"""Archangel improving itself (M14): the owner asks for a change by text ("add a Telegram channel"), or asks why
something happens, and Claude Code does it in GitHub Actions on the owner's Claude subscription
(`.github/workflows/improve.yml`), so it costs a workflow run, not API tokens. A change comes back as a PR for the owner
to merge (Railway deploys main); a question comes back answered.

Archangel holds only a GitHub token for this one repo (start the workflow, read its runs, merge). The Claude token lives
in the repo's secrets, never here. The repo, the runs and the PRs are public: what's asked must say nothing personal.
"""

import asyncio
import io
import json
import time
import zipfile
from collections.abc import Callable
from datetime import datetime

import httpx

REPO = "pTinosq/archangel"
WORKFLOW = "improve.yml"
POLL = 30
"""Seconds between looks at a run: a change takes minutes."""
FOUND = 120
"""Seconds to wait for a started run to show up in the list."""


def github(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        follow_redirects=True,  # an artifact's download is a redirect to storage
        timeout=30,
    )


class CodeError(Exception):
    pass


class Coder:
    def __init__(self, client: httpx.AsyncClient, repo: str = REPO, poll: float = POLL):
        self._client = client
        self._repo = repo
        self._poll = poll

    async def change(self, request: str, branch: str, record: Callable[[str], None]) -> tuple[bool, str]:
        """Has Claude Code make the change (or answer the question) on `branch`, carrying on its PR if there is one.
        Returns whether it worked, and what to tell the owner: the PR, or the answer, or what failed."""
        started = time.time()
        await self._call(
            "POST",
            f"/actions/workflows/{WORKFLOW}/dispatches",
            json={"ref": "main", "inputs": {"request": request, "branch": branch}},
        )
        run = await self._run(branch, started)
        record(f"Claude Code is on it: {run['html_url']}")
        while run["status"] != "completed":
            await asyncio.sleep(self._poll)
            run = await self._call("GET", f"/actions/runs/{run['id']}")
        if run["conclusion"] != "success":
            return False, f"The run {run['conclusion']}: {run['html_url']}"
        result = await self._result(run["id"])
        if not result["pr"]:
            return True, f"No change made. {result['summary']}"
        tests = "tests pass" if result["tests"] == "passed" else "TESTS FAIL"
        return True, f"{result['pr']} ({tests})\n{result['title']}\n{result['summary']}"

    async def branch(self, pr: int) -> str:
        """The branch of PR #`pr`, to carry it on."""
        return (await self._call("GET", f"/pulls/{pr}"))["head"]["ref"]

    async def merge(self, pr: int) -> str:
        """Squash-merges PR #`pr` into main, which deploys it."""
        found = await self._call("GET", f"/pulls/{pr}")
        if found["state"] != "open":
            return f"PR #{pr} is {'merged' if found.get('merged') else found['state']} already."
        await self._call("PUT", f"/pulls/{pr}/merge", json={"merge_method": "squash"})
        return f"Merged PR #{pr} ({found['title']}); it deploys in a few minutes."

    async def _run(self, branch: str, started: float) -> dict:
        """The run just started: the workflow names each run by its branch."""
        while True:
            runs = await self._call(
                "GET", f"/actions/workflows/{WORKFLOW}/runs", params={"event": "workflow_dispatch", "per_page": 20}
            )
            for run in runs["workflow_runs"]:
                if run["display_title"] == branch and _seconds(run["created_at"]) >= started - 60:
                    return run
            if time.time() - started > FOUND:
                raise CodeError("The run never started.")
            await asyncio.sleep(min(self._poll, 5))

    async def _result(self, run: int) -> dict:
        artifacts = await self._call("GET", f"/actions/runs/{run}/artifacts")
        found = next((a for a in artifacts["artifacts"] if a["name"] == "result"), None)
        if not found:
            raise CodeError("The run left no result.")
        response = await self._client.get(found["archive_download_url"])
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return json.loads(archive.read("result.json"))

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        response = await self._client.request(method, f"/repos/{self._repo}{path}", **kwargs)
        if response.is_error:
            raise CodeError(f"GitHub said {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}


def _seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()
