"""Cluster-aware cursor movement and deletion.

The cursor is a code-point offset into Editor.text, but every movement and
deletion snaps to grapheme cluster boundaries, so combining marks, ZWJ
emoji, flags and variation selectors are never split.
"""
from grapheme import next_boundary, prev_boundary

CURSOR = "│"


class Editor:
    def __init__(self, text=""):
        self.text = text
        self.cursor = len(text)

    # -- movement ---------------------------------------------------------
    def move_left(self):
        self.cursor = prev_boundary(self.text, self.cursor)

    def move_right(self):
        self.cursor = next_boundary(self.text, self.cursor)

    def home(self):
        self.cursor = 0

    def end(self):
        self.cursor = len(self.text)

    # -- editing ----------------------------------------------------------
    def _snap_left(self):
        """Deletion/insertion can merge the clusters adjacent to the edit
        point (e.g. removing a bidi control lets a combining mark or ZWJ
        reattach). Move the cursor to the boundary before its current spot."""
        from grapheme import is_boundary
        if self.cursor < len(self.text) and not is_boundary(self.text, self.cursor):
            self.cursor = prev_boundary(self.text, self.cursor)

    def insert(self, s):
        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
        self.cursor += len(s)
        self._snap_left()

    def backspace(self):
        """Delete the cluster before the cursor."""
        p = prev_boundary(self.text, self.cursor)
        self.text = self.text[:p] + self.text[self.cursor:]
        self.cursor = p
        self._snap_left()

    def delete_forward(self):
        """Delete the cluster after the cursor."""
        n = next_boundary(self.text, self.cursor)
        self.text = self.text[:self.cursor] + self.text[n:]
        self._snap_left()

    # -- inspection -------------------------------------------------------
    def render(self):
        """Text with the cursor shown as │."""
        return self.text[:self.cursor] + CURSOR + self.text[self.cursor:]

    def clusters(self):
        from grapheme import segment
        return segment(self.text)


def run(ops, text=""):
    """Apply ops and return [(op_label, rendered_state), ...] per step.

    Each op is a (name, arg) tuple; arg may be omitted for nullary ops.
    The initial state is included with the label '<init>'.
    """
    ed = Editor(text)
    history = [("<init>", ed.render())]
    for op in ops:
        name, arg = (op + (None,))[:2]
        getattr(ed, name)() if arg is None else getattr(ed, name)(arg)
        label = f"{name}({arg!r})" if arg is not None else f"{name}()"
        history.append((label, ed.render()))
    return history
