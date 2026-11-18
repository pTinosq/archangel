import asyncio

import pytest
from fakes import FakeChannel, FakeConnectors, FakeHelper, FakePilot, FakeReporter, FakeResearcher, devices, threads
from pydantic_ai.models.function import FunctionModel

from agent.events import OwnerMessage
from agent.gateway import Gateway
from agent.location import Whereabouts
from agent.messages import Message, Text
from agent.orchestrator import Orchestrator
from agent.styles import Audience, Style, Styles, parse
from agent.tasks import Tasks
from agent.twin.graph import Graph
from agent.web import Web
from test_orchestrator import say


def folder(tmp_path, **files: str):
    """A styles folder with a base, a default and `files` (name → front matter lines, then the text)."""
    files = {"base": 'about = "b"\n+++\nBASE', "default": 'about = "d"\n+++\nDEFAULT', **files}
    tmp_path.mkdir(exist_ok=True)
    for name, source in files.items():
        (tmp_path / f"{name}.md").write_text(f"+++\n{source}\n")
    return tmp_path


def chosen(styles: Styles, to: str = "", via: str = "") -> str:
    return styles.choose(Audience.of(to, via)).name


def test_the_starter_styles_go_to_who_they_are_for():
    styles = Styles()
    assert chosen(styles, "owner", "sms") == "owner"
    assert chosen(styles, "owner", "panel") == "owner"
    assert chosen(styles, "owner", "voice") == "owner-voice"  # more specific than owner alone
    assert chosen(styles, "The Owner") == "owner"
    assert chosen(styles, "Sam", "Discord") == "discord"
    assert chosen(styles, "Priya", "Gmail") == "semi-formal"
    assert chosen(styles, "Priya", "email") == "semi-formal"
    assert chosen(styles, "Sam", "WhatsApp") == "texting"
    assert chosen(styles, "Sam", "Bluesky") == "default"  # nothing fits
    assert chosen(styles) == "default"


def test_every_style_is_the_base_then_its_own_text():
    styles = Styles()
    for style in [*styles.profiles, styles.default]:
        guidance = styles.guidance(Audience.of("owner" if style.owner else "", style.via[0] if style.via else ""))
        assert guidance.index(styles.base.text) < guidance.index(style.text)
        assert f'"{style.name}"' in guidance.splitlines()[0]
    assert "Say each thing once" in styles.base.text


def test_every_style_says_in_a_line_who_it_is_for():
    assert "owner-voice: To the owner, on a call" in Styles().describe()
    assert "(via discord)" in Styles().describe()
    assert "default: " in Styles().describe().splitlines()[-1] and "(otherwise)" in Styles().describe()


def test_the_most_specific_style_wins_the_person_then_the_owner_then_where(tmp_path):
    styles = Styles(
        folder(
            tmp_path,
            where='about = "w"\nvia = ["discord"]\n+++\nW',
            owner='about = "o"\nowner = true\n+++\nO',
            sam='about = "s"\npeople = ["Sam"]\n+++\nS',
            sam_email='about = "e"\npeople = ["sam"]\nvia = ["email"]\n+++\nE',
        )
    )
    assert chosen(styles, "owner", "discord") == "owner"  # the owner beats where
    assert chosen(styles, "Sam", "discord") == "sam"  # a person beats where, however they're written
    assert chosen(styles, "Sam", "email") == "sam_email"  # naming more of what's known beats naming less
    assert chosen(styles, "Tom", "discord") == "where"
    assert chosen(styles, "Tom", "email") == "default"


def twin(store) -> Graph:
    """A made-up owner's people: Sam (also "Sam Taylor") in Uni friends and Climbing, Tom in Climbing, which links to
    him rather than he to it, and Priya in nothing."""
    graph = Graph(store.db)
    for name in ("People", "Sam", "Tom", "Priya", "Uni friends", "Climbing"):
        graph.add(name)
    graph.alias("Sam", "Sam Taylor")
    graph.alias("Uni friends", "uni lot")
    graph.link("Sam", "Uni friends", "in", "from uni")
    graph.link("Sam", "Climbing", "in", "climbs")
    graph.link("Climbing", "Tom", "member", "climbs")
    for name in ("Sam", "Tom", "Priya"):
        graph.link("People", name, "friend", "a friend")
    return graph


def test_a_group_is_a_node_the_twin_links_them_to(tmp_path, store):
    styles = Styles(
        folder(
            tmp_path / "styles",
            uni='about = "u"\ngroups = ["uni lot"]\n+++\nU',
            climbing='about = "c"\ngroups = ["Climbing"]\n+++\nC',
            climbing_email='about = "ce"\ngroups = ["climbing"]\nvia = ["email"]\n+++\nCE',
            where='about = "w"\nvia = ["discord"]\n+++\nW',
            sam_email='about = "s"\npeople = ["sam"]\nvia = ["email"]\n+++\nS',
        ),
        twin=twin(store),
    )
    assert chosen(styles, "Tom", "discord") == "climbing"  # a group beats where, linked either way
    assert chosen(styles, "Tom", "email") == "climbing_email"
    assert chosen(styles, "Sam Taylor", "discord") == "climbing"  # in two that tie: the first by name
    assert chosen(styles, "Sam Taylor", "email") == "sam_email"  # an alias finds the profile naming them
    assert chosen(styles, "Priya", "discord") == "where"
    assert chosen(styles, "Someone new", "discord") == "where"
    assert chosen(Styles(tmp_path / "styles"), "Tom", "discord") == "where"  # no twin: no groups


def test_what_a_style_names_must_all_fit(tmp_path):
    styles = Styles(folder(tmp_path, both='about = "x"\nowner = true\nvia = ["sms"]\n+++\nX'))
    assert chosen(styles, "owner", "sms") == "both"
    assert chosen(styles, "owner", "voice") == "default"
    assert chosen(styles, "Sam", "sms") == "default"


def test_a_new_style_is_one_file(tmp_path):
    styles = Styles(folder(tmp_path))
    assert chosen(styles, "Tom", "telegram") == "default"
    folder(tmp_path, telegram='about = "t"\nvia = ["telegram"]\n+++\nT')
    assert chosen(Styles(tmp_path), "Tom", "telegram") == "telegram"


@pytest.mark.parametrize(
    "files, problem",
    [
        ({"x": 'about = "x"\n+++\nX'}, "names no one"),
        ({"x": 'about = "x"\nvia = ["a"]\n+++\nX', "y": 'about = "y"\nvia = ["a", "b"]\n+++\nY'}, "could both be chosen"),
        ({"x": 'about = "x"\nowner = true\n+++\nX', "y": 'about = "y"\nowner = true\n+++\nY'}, "could both be chosen"),
        ({"x": 'about = "x"\ngroups = ["Uni"]\n+++\nX', "y": 'about = "y"\ngroups = ["uni"]\n+++\nY'}, "could both"),
        ({"x": 'about = "x"\nvai = ["a"]\n+++\nX'}, "unknown vai"),
        ({"x": 'via = ["a"]\n+++\nX'}, "`about`"),
        ({"x": 'about = "x"\nvia = "a"\n+++\nX'}, "`via` is a list"),
        ({"x": 'about = "x"\nowner = "yes"\n+++\nX'}, "`owner` is true or false"),
        ({"x": 'about = "x"\nvia = ["a"]\n+++\n  \n'}, "nothing written"),
        ({"default": 'about = "d"\nowner = true\n+++\nD'}, "names no audience"),
        ({"base": 'about = "b"\nvia = ["a"]\n+++\nB'}, "names no audience"),
    ],
)
def test_a_style_that_would_misbehave_is_refused_at_start(tmp_path, files, problem):
    with pytest.raises(ValueError, match=problem):
        Styles(folder(tmp_path, **files))


def test_styles_that_cannot_clash_may_share_a_name(tmp_path):
    folder(
        tmp_path,
        a='about = "a"\nvia = ["discord"]\n+++\nA',
        b='about = "b"\nowner = true\nvia = ["discord"]\n+++\nB',  # names the owner too
        c='about = "c"\npeople = ["Sam", "Tom"]\nvia = ["sms"]\n+++\nC',
        d='about = "d"\npeople = ["Sam"]\nvia = ["email"]\n+++\nD',  # same person, another place
    )
    assert [s.name for s in Styles(tmp_path).profiles] == ["a", "b", "c", "d"]


def test_the_owners_own_folder_adds_styles_and_replaces_built_in_ones(tmp_path):
    built_in = folder(tmp_path / "built-in", discord='about = "d"\nvia = ["discord"]\n+++\nBuilt-in')
    own = tmp_path / "own"
    own.mkdir()
    (own / "discord.md").write_text('+++\nabout = "mine"\nvia = ["discord"]\n+++\nMine\n')
    (own / "sam.md").write_text('+++\nabout = "s"\npeople = ["Sam"]\n+++\nFor Sam\n')

    styles = Styles(built_in, own)
    assert chosen(styles, "Tom", "discord") == "discord" and styles.choose(Audience.of("Tom", "discord")).text == "Mine"
    assert chosen(styles, "Sam", "discord") == "sam"
    assert chosen(Styles(built_in, tmp_path / "none"), "Sam", "discord") == "discord"  # no folder: fine


def test_the_base_and_default_are_required(tmp_path):
    (tmp_path / "default.md").write_text('+++\nabout = "d"\n+++\nD\n')
    with pytest.raises(ValueError, match="base.md is missing"):
        Styles(tmp_path)


def test_a_file_must_open_with_its_settings():
    with pytest.raises(ValueError, match="starts with TOML"):
        parse("x", "Just prose.")
    with pytest.raises(ValueError, match="x:"):
        parse("x", "+++\nnot toml\n+++\nText")


def test_a_style_names_what_it_is_for_in_a_few_words():
    assert Style("x", "x", "X", owner=True, people=("sam",), via=("sms", "voice")).applies == (
        "the owner; to sam; via sms, voice"
    )


def test_the_audience_is_what_a_model_says():
    assert Audience.of("owner", "SMS") == Audience(True, "", "SMS")
    assert Audience.of("the owner") == Audience(True)
    assert Audience.of(" Sam Taylor ", "email") == Audience(False, "Sam Taylor", "email")
    assert str(Audience(True, via="voice")) == "the owner via voice"
    assert str(Audience(person="Sam")) == "Sam" and str(Audience()) == "someone"


def test_agents_ask_for_a_style_by_who_and_where():
    ask = Styles().tools.tools["writing_style"].function
    assert '"discord", for Sam via Discord' in asyncio.run(ask(to="Sam", via="Discord"))
    assert '"default", for someone' in asyncio.run(ask())


def test_the_orchestrator_is_given_the_style_for_where_its_reply_goes(store):
    seen = []

    def model(messages, info):
        seen.append(info.instructions)
        return say("ok")

    channels = FakeChannel("sms"), FakeChannel("voice")
    tasks = Tasks(store, {"phone": FakePilot()}, FakeResearcher(), FakeReporter(), FakeHelper())
    orchestrator = Orchestrator(
        FunctionModel(model),
        store,
        tasks,
        Gateway(list(channels), store),
        FakeResearcher(),
        FakeConnectors(),
        devices(),
        Whereabouts(store, Web(), None),
        threads(store),
        styles=Styles(),
    )
    for channel in channels:
        asyncio.run(orchestrator.handle(OwnerMessage(Message(channel.owner, (Text("hi"),)))))

    by_text, by_voice = seen
    assert 'Writing style "owner", for the owner via sms' in by_text and "Say each thing once" in by_text
    assert 'Writing style "owner-voice"' in by_voice and 'Writing style "owner",' not in by_voice
