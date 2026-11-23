import asyncio

import httpx
from fakes import FakeHelper, FakePilot, FakeReporter, FakeResearcher, threads
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from test_panel import Panel

from agent.channels.login import GitHub
from agent.messages import Address, Image, Message, Text
from agent.reports import Report, Reporter
from agent.research import TAVILY, Researcher
from agent.tasks import Tasks

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG = b"\xff\xd8\xff" + b"0" * 9
PUBLIC = "93.184.216.34"


def researcher(handler, resolve=None, tavily=None) -> Researcher:
    async def public(host):
        return [PUBLIC]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    found = httpx.AsyncClient(transport=httpx.MockTransport(tavily)) if tavily else None
    return Researcher(None, None, found, client, resolve or public)  # type: ignore[arg-type]


def test_a_report_shows_only_pictures_it_has_and_drops_other_images():
    report = Report(
        tldr="t",
        title="Kettles",
        pages=['<p>A</p><img src="img1.png" alt="Red <kettle>"><img src="https://evil.example/x.png">'],
    ).attach({"img1.png": Image(PNG, "image/png")})
    html = report.page("/reports/7/images/")
    assert '<figure><img src="/reports/7/images/img1.png" alt="Red &lt;kettle&gt;" loading="lazy">' in html
    assert "<figcaption>Red &lt;kettle&gt;</figcaption>" in html
    assert "evil.example" not in html
    assert list(report.images) == ["img1.png"]


def test_pictures_the_pages_dont_use_are_not_kept():
    report = Report(tldr="t", title="T", pages=["<p>None</p>"]).attach({"img1.png": Image(PNG, "image/png")})
    assert report.images == {}


def test_the_reporter_picks_its_pictures_from_the_web_and_the_owner():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gone.png":
            return httpx.Response(404)
        return httpx.Response(200, content=PNG, headers={"content-type": "text/html"})  # the bytes decide

    steps = iter(
        [
            ("add_image", {"source": "https://shop.example/kettle.png", "alt": "A kettle"}),
            ("add_image", {"source": "owner 1", "alt": "The owner's mug"}),
            ("add_image", {"source": "https://shop.example/gone.png", "alt": "Missing"}),
        ]
    )
    results = []

    def model(messages, info) -> ModelResponse:
        results.append(messages[-1].parts)
        if step := next(steps, None):
            return ModelResponse(parts=[ToolCallPart(*step)])
        pages = ['<img src="img1.png" alt="A kettle"><img src="img2.jpg" alt="Mug">']
        report = {"tldr": "Kettle.", "title": "Kettles", "pages": pages}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, report)])

    reporter = Reporter(FunctionModel(model), researcher=researcher(handler))
    report = asyncio.run(reporter.write("kettles", "Material", [Image(JPEG, "image/jpeg")]))
    assert set(report.images) == {"img1.png", "img2.jpg"}
    assert "owner 1 to owner 1" in results[0][-1].content  # told what the owner sent
    assert "Added as img1.png" in str(results[1])
    assert "Couldn't get" in str(results[3])  # and the missing one is only a note


def test_a_report_without_pictures_is_text():
    def model(messages, info) -> ModelResponse:
        report = {"tldr": "t", "title": "T", "pages": ["<p>x</p>"]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, report)])

    report = asyncio.run(Reporter(FunctionModel(model)).write("t", "m"))
    assert report.images == {} and "<img" not in report.html


def fetch(url, handler=lambda r: httpx.Response(200, content=PNG), resolve=None):
    return asyncio.run(researcher(handler, resolve).image(url))


def test_pictures_that_cant_be_fetched_are_refused():
    async def private(host):
        return ["10.0.0.5"]

    async def selective(host):
        return ["10.0.0.5"] if host == "internal.example" else [PUBLIC]

    def redirect(request):
        if request.url.host == "shop.example":
            return httpx.Response(302, headers={"location": "http://internal.example/a.png"})
        return httpx.Response(200, content=PNG)

    assert fetch("https://shop.example/a.png") == Image(PNG, "image/png")
    assert fetch("https://internal.example/a.png", resolve=private) is None  # not the internet
    assert fetch("file:///etc/passwd") is None
    assert fetch("https://shop.example/a", lambda r: httpx.Response(200, content=b"<html>")) is None
    assert fetch("https://shop.example/a", lambda r: httpx.Response(404)) is None
    assert fetch("https://shop.example/a", lambda r: httpx.Response(200, content=PNG + b"0" * 5_000_000)) is None
    assert fetch("https://shop.example/a.png", redirect, selective) is None  # a redirect is checked too


def test_images_come_from_search_results_and_the_pages_read():
    def tavily(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{TAVILY}/search":
            images = [{"url": "https://a.example/1.jpg", "description": "A red kettle"}]
            return httpx.Response(200, json={"results": [], "images": images})
        return httpx.Response(200, json={"results": [{"url": "u", "raw_content": "", "images": ["https://b.example/2.png"]}]})

    found = researcher(lambda r: httpx.Response(404), tavily=tavily)
    assert asyncio.run(found.image_search("red kettle")) == "https://a.example/1.jpg — A red kettle"
    assert asyncio.run(found.page_images("https://b.example")) == "https://b.example/2.png"
    assert "isn't set up" in asyncio.run(researcher(lambda r: httpx.Response(404)).image_search("x"))


def test_a_reports_pictures_stay_with_it_and_follow_its_visibility(store):
    class WithPictures(FakeReporter):
        async def write(self, topic, material, supplied=()):
            self.supplied = list(supplied)
            pages = ['<img src="img1.png" alt="Kettle">']
            return Report(tldr="t", title=topic, pages=pages).attach({"img1.png": Image(PNG, "image/png")})

    async def start():
        owner = Address("cli", "local")
        mug = store.save_image(Image(JPEG, "image/jpeg"))
        thread = threads(store).store.new("Kettles")
        store.add_line(Message(owner, (Text("my mug"), Image(JPEG, "image/jpeg")), thread=thread.id), "owner")
        reporter = WithPictures()
        tasks = Tasks(store, {"phone": FakePilot()}, FakeResearcher(), reporter, FakeHelper())
        tasks.start("kettles", owner, "research", thread.id)
        await tasks.idle()
        return reporter, mug

    reporter, _ = asyncio.run(start())
    assert [image.media_type for image in reporter.supplied] == ["image/jpeg"]  # what the owner sent is on offer

    assert 'src="/reports/1/images/img1.png"' in store.page(1)
    assert store.page_image(1, "img1.png") == Image(PNG, "image/png")
    assert store.page_image(1, "../1.html") is None and store.page_image(2, "img1.png") is None

    panel = Panel(store, GitHub("id", "secret", "owner"))
    stranger = lambda url: panel.call("GET", url, host="archangel.up.railway.app")  # noqa: E731
    assert stranger("/reports/1/images/img1.png").status_code == 307  # private: sign in first
    store.make_public(1, True)
    shown = stranger("/reports/1/images/img1.png")
    assert (shown.status_code, shown.content, shown.headers["content-type"]) == (200, PNG, "image/png")
    assert "private" in shown.headers["cache-control"]
    assert "img-src 'self'" in stranger("/reports/1").headers["content-security-policy"]
    assert stranger("/reports/1/images/other.png").status_code == 404
    assert stranger("/reports/1/images/img1.png/x").status_code != 200
    store.make_public(1, False)
    assert stranger("/reports/1/images/img1.png").status_code == 307
