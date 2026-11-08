"""Reads AndroidControl's accessibility forests (AndroidAccessibilityForest in protobuf text format).

Only what the converter needs: every node with its window (type, layer), bounds and the words a person sees on it.
The text format escapes non-ASCII bytes as octal (\\303\\251); strings are decoded back to UTF-8.
"""
import re

_SCALAR = re.compile(r'^\s*(\w+):\s*(.*)$')
_ESC = re.compile(rb'\\(?:([0-7]{1,3})|x([0-9a-fA-F]{2})|(.))', re.S)
_SIMPLE = {b'n': b'\n', b't': b'\t', b'r': b'\r', b'"': b'"', b"'": b"'", b'\\': b'\\', b'a': b'\a', b'b': b'\b',
           b'f': b'\f', b'v': b'\v'}


def unquote(raw):
    """A quoted text-format string ("\\303\\251t\\303\\251") as text ("été")."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    data = s.encode("utf-8", "surrogateescape")

    def one(m):
        if m.group(1) is not None:
            return bytes([int(m.group(1), 8) & 0xFF])
        if m.group(2) is not None:
            return bytes([int(m.group(2), 16)])
        return _SIMPLE.get(m.group(3), m.group(3))
    return _ESC.sub(one, data).decode("utf-8", "replace")


class Node:
    __slots__ = ("left", "top", "right", "bottom", "cls", "text", "desc", "hint", "pkg", "rid", "clickable",
                 "editable", "scrollable", "depth", "wtype", "layer", "order", "checkable")

    def __init__(self):
        self.left = self.top = self.right = self.bottom = 0
        self.cls = self.text = self.desc = self.hint = self.pkg = self.rid = ""
        self.clickable = self.editable = self.scrollable = self.checkable = False
        self.depth = 0
        self.wtype = ""
        self.layer = 0
        self.order = 0

    def area(self):
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom

    def label(self):
        for v in (self.text, self.desc, self.hint):
            v = (v or "").strip()
            if v:
                return re.sub(r"\s+", " ", v)
        return ""

    def short_id(self):
        return self.rid.split("/", 1)[-1] if self.rid else ""


def parse(text):
    """Every node of every window, in document order."""
    nodes = []
    stack = []                 # names of the open blocks
    node = None
    wtype, layer = "", 0
    pending_windows = []       # nodes of the current window (their window fields can come after the tree)
    order = 0
    for line in text.splitlines():
        st = line.strip()
        if not st:
            continue
        if st.endswith("{"):
            name = st[:-1].strip()
            stack.append(name)
            if name == "nodes":
                node = Node()
                node.order = order
                order += 1
            elif name == "windows":
                wtype, layer = "", 0
                pending_windows = []
            continue
        if st == "}":
            name = stack.pop() if stack else ""
            if name == "nodes" and node is not None:
                pending_windows.append(node)
                nodes.append(node)
                node = None
            elif name == "windows":
                for n in pending_windows:
                    n.wtype, n.layer = wtype, layer
                pending_windows = []
            continue
        m = _SCALAR.match(st)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        top = stack[-1] if stack else ""
        if top == "bounds_in_screen":
            if node is not None and len(stack) >= 2 and stack[-2] == "nodes":
                try:
                    setattr(node, key, int(value))
                except (ValueError, AttributeError):
                    pass
            continue
        if top == "nodes" and node is not None:
            if key == "class_name":
                node.cls = unquote(value)
            elif key == "text":
                node.text = unquote(value)
            elif key == "content_description":
                node.desc = unquote(value)
            elif key == "hint_text":
                node.hint = unquote(value)
            elif key == "package_name":
                node.pkg = unquote(value)
            elif key == "view_id_resource_name":
                node.rid = unquote(value)
            elif key == "is_clickable":
                node.clickable = value == "true"
            elif key == "is_editable":
                node.editable = value == "true"
            elif key == "is_scrollable":
                node.scrollable = value == "true"
            elif key == "is_checkable":
                node.checkable = value == "true"
            elif key == "depth":
                try:
                    node.depth = int(value)
                except ValueError:
                    pass
            continue
        if top == "windows":
            if key == "window_type":
                wtype = value.strip()
            elif key == "layer":
                try:
                    layer = int(value)
                except ValueError:
                    pass
    return nodes


SYSTEM_PACKAGES = ("com.android.systemui", "com.google.android.apps.nexuslauncher", "com.android.launcher3",
                   "com.google.android.inputmethod.latin", "com.android.inputmethod.latin",
                   "com.google.android.permissioncontroller", "com.android.permissioncontroller",
                   "com.google.android.packageinstaller", "android")


def app_package(nodes):
    """The package of the app window on this screen ("" for the launcher or system screens)."""
    count = {}
    for n in nodes:
        if n.wtype == "TYPE_APPLICATION" and n.pkg and n.pkg not in SYSTEM_PACKAGES:
            count[n.pkg] = count.get(n.pkg, 0) + 1
    return max(count, key=count.get) if count else ""


def target(nodes, x, y):
    """(label, resource id, kind) of what a tap at x,y hits: the topmost window, the smallest node there; its words or,
    for an unnamed container, the words inside it. kind is 'field', 'key' (the keyboard) or 'item'."""
    hits = [n for n in nodes if n.contains(x, y) and n.area() > 0]
    if not hits:
        return "", "", "item"
    top_layer = max(n.layer for n in hits)
    if any(n.wtype == "TYPE_INPUT_METHOD" for n in hits):
        top = [n for n in hits if n.wtype == "TYPE_INPUT_METHOD"]
    else:
        top = [n for n in hits if n.layer == top_layer] or hits
    top.sort(key=lambda n: (n.area(), -n.depth))
    kind = "key" if top[0].wtype == "TYPE_INPUT_METHOD" else ("field" if any(n.editable for n in top[:3]) else "item")
    rid = next((n.rid for n in top if n.rid), "")
    for n in top:
        lab = n.label()
        if lab:
            return lab, rid, kind
        if n.area() > 0.35 * 1080 * 2400:
            break
    # an unnamed button: the words of what lies inside it (a TextView in a clickable row)
    smallest = top[0]
    inside = [n for n in nodes if n.layer == smallest.layer and n is not smallest and n.label()
              and smallest.left <= n.left and n.right <= smallest.right and smallest.top <= n.top
              and n.bottom <= smallest.bottom]
    inside.sort(key=lambda n: n.order)
    if inside and len(inside) <= 4:
        return inside[0].label(), rid, kind
    return "", rid, kind
