"""`just style [to] [via]`: which style an audience gets, and the text a model would be given. No arguments lists them
all. e.g. `just style owner sms`, `just style Sam discord`. Groups are looked up in the local twin."""

import sys

from agent.store import DATA, Store
from agent.styles import Audience, Styles
from agent.twin.graph import Graph


def main(args: list[str]) -> None:
    styles = Styles(own=DATA / "styles", twin=Graph(Store().db))
    if not args:
        print(styles.describe())
        return
    print(styles.guidance(Audience.of(*args[:2])))


main(sys.argv[1:])
