"""Every text-mode file read/write must name its encoding.

Without it Python uses the platform default: UTF-8 on Linux/macOS, but cp1252 on Windows,
which cannot encode characters found in the reports (e.g. the "ﬁ" ligature, U+FB01).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _offending_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name not in ("read_text", "write_text", "open"):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ("pymupdf", "fitz", "psycopg"):
            continue  # not file text IO
        if any(k.arg == "encoding" for k in node.keywords):
            continue
        if name == "open":
            # mode is the first positional for Path.open, the second for builtin open()
            args = node.args if isinstance(node.func, ast.Attribute) else node.args[1:]
            modes = [a.value for a in args[:1] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            if any("b" in m for m in modes):
                continue  # binary mode has no encoding
            mode_is_variable = not modes and (args[:1] or any(k.arg == "mode" for k in node.keywords))
            if mode_is_variable:
                continue  # e.g. scrape.py's part.open(mode) with mode "ab"/"wb"; reviewed by hand
            # no mode at all: open() and Path.open() default to text mode, so flag it
        bad.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}()")
    return bad


def test_all_text_io_names_an_encoding():
    files = [p for d in ("app", "scripts", "tests") for p in (ROOT / d).rglob("*.py")]
    bad = [b for f in files for b in _offending_calls(f)]
    assert not bad, "text IO without encoding=:\n" + "\n".join(bad)
