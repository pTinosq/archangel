"""The foreground window's accessibility tree, from UI Automation: Windows' counterpart of Android's node tree."""

import uiautomation as auto

from bridge.protocol import Message, node

MAX_DEPTH = 30
MAX_NODES = 500
"""Each property is a call into the app's process: a whole browser page would outlast the relay's 15 s."""


def tree() -> list[Message]:
    """Call on a thread of its own: UI Automation sets COM up per thread."""
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl()
        if not root:
            return []
        budget = [MAX_NODES]
        found = _walk(root, 0, budget)
        return [found] if found else []


def _walk(control: auto.Control, depth: int, budget: list[int]) -> Message | None:
    budget[0] -= 1
    try:
        rect = control.BoundingRectangle
        flags = []
        if control.GetPattern(auto.PatternId.InvokePattern) or control.GetPattern(auto.PatternId.TogglePattern):
            flags.append("clickable")
        value = control.GetPattern(auto.PatternId.ValuePattern)
        if value and not value.IsReadOnly:
            flags.append("editable")
        if control.GetPattern(auto.PatternId.ScrollPattern):
            flags.append("scrollable")
        if control.HasKeyboardFocus:
            flags.append("focused")
        kind = control.ControlTypeName.removesuffix("Control")
        name, help, id = control.Name, control.HelpText, control.AutomationId
    except Exception:  # it closed while being read
        return None
    children = []
    if depth < MAX_DEPTH:
        child = control.GetFirstChildControl()
        while child and budget[0] > 0:
            if found := _walk(child, depth + 1, budget):
                children.append(found)
            child = child.GetNextSiblingControl()
    return node(kind, (rect.left, rect.top, rect.right, rect.bottom), name, help, id, flags, children)
