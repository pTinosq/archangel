"""`just style [to] [via]`: which style an audience gets, and the text a model would be given. No arguments lists them
all. e.g. `just style owner sms`, `just style Sam discord`. Groups are looked up in the local twin; the text is as
edited on the panel's Prompts tab, if it was."""

import sys

from agent.prompts import Prompts
from agent.store import DATA, Store
from agent.styles import Audience, Styles
from agent.twin.graph import Graph


def main(args: list[str]) -> None:
    store = Store()
    styles = Styles(own=DATA / "styles", twin=Graph(store.db), prompts=Prompts(store))
    if not args:
        print(styles.describe())
        return
    print(styles.guidance(Audience.of(*args[:2])))


main(sys.argv[1:])
