import pytest

from agent.twin.graph import ROOT, Graph


@pytest.fixture
def graph(store) -> Graph:
    now = iter(range(1000))
    return Graph(store.db, clock=lambda: next(now))


def test_you_is_always_there(graph, store):
    assert graph.find("you").name == ROOT
    assert [node.name for node in Graph(store.db).nodes()] == [ROOT]  # once, however often it starts
    with pytest.raises(ValueError):
        graph.delete(ROOT)


def test_a_name_or_alias_is_one_node_in_any_case(graph):
    graph.add("Helen", "Your mother. Birthday 14 May.")
    graph.alias("Helen", "Mum")
    assert graph.find("mum").name == "Helen"
    for taken in ("helen", "MUM"):
        with pytest.raises(ValueError, match="already Helen"):
            graph.add(taken)


def test_a_node_reads_as_its_page_and_previewed_links(graph):
    graph.add("Travel")
    graph.add("Uber", "Rides around London and to work.")
    graph.link(ROOT, "Travel", "area", "how you get around")
    graph.link("Travel", "Uber", "uses", "rides: cheapest, 5★, no tip")
    assert graph.open("travel") == (
        "# Travel\n(a router: no page, only links)\n\n→ Uber (uses): rides: cheapest, 5★, no tip\n\nLinked from: You"
    )


def test_a_hub_is_one_node_linked_from_everywhere(graph):
    for area in ("Travel", "Work", "Uber"):
        graph.add(area)
    graph.link("Travel", "Uber", "uses", "rides")
    graph.link("Work", "Uber", "expenses", "business rides, receipts")
    assert [link.source for link in graph.links_to("Uber")] == ["Travel", "Work"]


def test_a_changed_page_leaves_previews_of_it_stale_until_rewritten(graph):
    graph.add("Flights")
    graph.link(ROOT, "Flights", "travels by", "aisle seat")
    assert graph.stale() == []
    graph.write("Flights", "Aisle seat. No mornings.", said="I hate morning flights", origin="chat")
    assert [(link.source, link.target) for link in graph.stale()] == [(ROOT, "Flights")]
    graph.link(ROOT, "Flights", "travels by", "aisle seat, never mornings")
    assert graph.stale() == []


def test_every_edit_keeps_the_owners_words(graph):
    graph.add("Flights", origin="task 3")
    graph.write("Flights", "No mornings.", said="I hate morning flights", origin="task 3")
    latest = graph.history("Flights")[0]
    assert (latest.what, latest.before, latest.after) == ("page", "", "No mornings.")
    assert (latest.said, latest.origin) == ("I hate morning flights", "task 3")


def test_merging_moves_names_and_links_to_the_one_kept(graph):
    for name in ("Helen", "Mum", "Flowers"):
        graph.add(name)
    graph.link("Mum", "Flowers", "likes", "lilies")
    graph.link("Helen", "Mum", "same as", "?")  # would become a loop: dropped
    graph.merge("Mum", "Helen")
    assert graph.find("mum").name == "Helen"
    assert [(link.source, link.target) for link in graph.links()] == [("Helen", "Flowers")]
    assert "merged Mum" in graph.history("Helen")[0].what


def test_renaming_keeps_history_and_frees_the_old_name(graph):
    graph.add("Mom", "Your mother.")
    graph.rename("Mom", "Helen")
    assert graph.find("Mom") is None and graph.find("Helen").page == "Your mother."
    assert [change.what for change in graph.history("Helen")] == ["renamed", "added"]


def test_deleting_takes_its_links_and_names(graph):
    graph.add("Deliveroo")
    graph.alias("Deliveroo", "Roo")
    graph.link(ROOT, "Deliveroo", "orders from", "food")
    graph.delete("Deliveroo")
    assert graph.find("Roo") is None and graph.links() == []
    graph.add("Roo")  # free again
