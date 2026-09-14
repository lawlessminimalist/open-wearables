"""Stable hashes of Python function/method bodies, for symbol-level drift detection.

Why this exists
---------------
`check_upstream.py` used to flag a patch as a shadow risk whenever upstream touched
the *file* a patch replaces a method in. In the 2026-09-13 reconcile two of three
such flags were false positives (upstream had edited other methods in the same
file), and each false positive cost a full body-diff audit. Hashing the replaced
method itself, not the file, removes that class of noise and also gives the guard
test `backend/tests/test_ow_patches_shadow_drift.py` a deterministic, post-merge
check: if upstream's method body no longer matches the hash recorded when the
patch was last rebased, the test fails and names the symbol.

What is hashed
--------------
The `ast.dump` of the function node with its docstring removed, so comments,
formatting and docstring edits do not count as drift. Any change to the code
itself (a new kwarg, a new column, a reordered SELECT) changes the hash.

Two flavours:
  body_hash      - the whole function (decorators excluded)
  signature_hash - just the argument list, for `decorate`-kind patches whose
                   composer calls upstream positionally and would silently
                   mis-position a new parameter.

Used from both `check_upstream.py` (against `upstream/main` via `git show`) and the
pytest guard (against the working tree). Stdlib only.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path

# "Class.method", ".method" (inherits the previous class), or "func"
_SYMBOL_TOKEN_RE = re.compile(r"^(?:(?P<cls>[A-Za-z_][A-Za-z0-9_]*)?\.)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)$")


def parse_symbols(symbol_field: str) -> list[str]:
    """Turn a PATCHES.md `symbol:` field into ["Class.method", ...].

    Accepts the shorthand the registry already uses:
      "A.x + .y + .z"            -> ["A.x", "A.y", "A.z"]
      "A.x + B.y"                -> ["A.x", "B.y"]
      "A.x (decorated)"          -> ["A.x"]   (parentheticals dropped)
      "DAILIES_SERIES (mapping)" -> []        (module constants are not functions)
    Tokens that are not function-like are skipped rather than guessed at.
    """
    cleaned = re.sub(r"\([^)]*\)", " ", symbol_field)
    out: list[str] = []
    last_cls: str | None = None
    for raw in re.split(r"\s*(?:\+|,)\s*", cleaned):
        tok = raw.strip()
        if not tok:
            continue
        m = _SYMBOL_TOKEN_RE.match(tok)
        if not m:
            continue
        cls, name = m.group("cls"), m.group("name")
        if name.isupper():  # DAILIES_SERIES, ACTIVITY_SAMPLE_SERIES: module constants, not functions
            continue
        if tok.startswith(".") and last_cls:
            cls = last_cls
        if cls:
            last_cls = cls
            out.append(f"{cls}.{name}")
        elif name[0].islower():  # bare function
            out.append(name)
    return out


def find_function(tree: ast.AST, symbol: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Locate `Class.method` (method of that class) or `func` (any def by that name)."""
    if "." in symbol:
        cls_name, meth = symbol.rsplit(".", 1)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == meth:
                        return sub
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            return node
    return None


def _strip_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    clone = ast.FunctionDef(
        name=node.name,
        args=node.args,
        body=body or [ast.Pass()],
        decorator_list=[],
        returns=node.returns,
        type_comment=None,
    )
    return clone


def body_hash(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    dumped = ast.dump(_strip_docstring(node), annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]


def signature_hash(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    dumped = ast.dump(node.args, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]


def source_at_ref(repo_root: Path, ref: str, path: str) -> str | None:
    """`git show <ref>:<path>`, or None if the path does not exist at that ref."""
    res = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=str(repo_root), capture_output=True, text=True, check=False
    )
    return res.stdout if res.returncode == 0 else None


def hash_symbols_in_source(source: str, symbols: list[str], *, signature_only: bool = False) -> dict[str, str | None]:
    """Map each symbol to its hash in `source` (None when the symbol is absent)."""
    tree = ast.parse(source)
    out: dict[str, str | None] = {}
    for sym in symbols:
        node = find_function(tree, sym)
        if node is None:
            out[sym] = None
        else:
            out[sym] = signature_hash(node) if signature_only else body_hash(node)
    return out
