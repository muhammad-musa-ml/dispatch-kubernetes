#!/usr/bin/env python3
"""Paste results/summary.md into README.md between the results markers, so no figure in the README is typed.

The first line of summary.md (its title) is dropped and the remaining headings move one level down.
Run `python bench/summarize.py` first. Standard library only.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN, END = "<!-- results:begin -->", "<!-- results:end -->"

summary = (ROOT / "results" / "summary.md").read_text(encoding="utf-8").splitlines()
body = [re.sub(r"^(#+) ", r"\1# ", ln) for ln in summary[1:]]
readme = (ROOT / "README.md").read_text(encoding="utf-8")
i, j = readme.index(BEGIN), readme.index(END)
new = readme[: i + len(BEGIN)] + "\n" + "\n".join(body).strip("\n") + "\n" + readme[j:]
(ROOT / "README.md").write_text(new, encoding="utf-8")
print("embedded", len(body), "lines of results/summary.md into README.md")
