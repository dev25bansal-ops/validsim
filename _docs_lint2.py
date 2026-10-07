"""Docs-truth check: balanced code fences + Obsidian callout nesting sanity."""

from __future__ import annotations

import pathlib

TARGETS = sorted(
    list(pathlib.Path("docs").glob("*.md"))
    + list(pathlib.Path("vault").rglob("*.md"))
    + [
        pathlib.Path("README.md"),
        pathlib.Path("CHANGELOG.md"),
        pathlib.Path("CONTRIBUTING.md"),
    ]
)

problems = 0
for f in TARGETS:
    text = f.read_text(encoding="utf-8")
    if text.count("```") % 2:
        print(f"UNBALANCED FENCE: {f}")
        problems += 1
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.startswith(">   > [!") and lines[i + 1].startswith(">") and not lines[i + 1].startswith(">   >"):
            print(f"NESTED CALLOUT LEAK: {f}:{i + 2}")
            problems += 1

# Every internal anchor I added must resolve to a real heading in the same file.
import re

for f in TARGETS:
    text = f.read_text(encoding="utf-8")
    headings = {
        re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
        for h in re.findall(r"^#{1,6} (.+)$", text, re.M)
    }
    for anchor in re.findall(r"\]\(#([a-z0-9-]+)\)", text):
        if anchor not in headings:
            print(f"DANGLING ANCHOR: {f} -> #{anchor}")
            problems += 1

print(f"checked {len(TARGETS)} files; problems: {problems}")
