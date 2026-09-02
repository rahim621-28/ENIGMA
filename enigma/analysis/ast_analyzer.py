"""Structural code analysis via Python's ast module.

Extracts function/class/method symbols with their source spans, and can
locate the symbol that contains a given (file, line) pair — this is how we
go from "line 3 raised ZeroDivisionError" to "the calculate_metrics function
is the suspect" without any LLM call.
"""
from __future__ import annotations

import ast
from pathlib import Path

from enigma.graph.state import Symbol


class SymbolExtractor(ast.NodeVisitor):
    def __init__(self, file_path: str, source_lines: list[str]):
        self.file_path = file_path
        self.source_lines = source_lines
        self.symbols: list[Symbol] = []
        self._class_stack: list[str] = []

    def _source_span(self, node: ast.AST) -> str:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        return "\n".join(self.source_lines[start:end])

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        kind = "method" if self._class_stack else "function"
        name = node.name if not self._class_stack else f"{self._class_stack[-1]}.{node.name}"
        self.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                source=self._source_span(node),
            )
        )
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.symbols.append(
            Symbol(
                name=node.name,
                kind="class",
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno) or node.lineno,
                source=self._source_span(node),
            )
        )
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()


def extract_symbols(file_path: str) -> list[Symbol]:
    """Parse a single Python file and return every function/class/method symbol."""
    text = Path(file_path).read_text()
    tree = ast.parse(text, filename=file_path)
    extractor = SymbolExtractor(file_path, text.splitlines())
    extractor.visit(tree)
    return extractor.symbols


def extract_symbols_from_repo(repo_path: str) -> list[Symbol]:
    """Walk a repo and extract symbols from every .py file, skipping venvs/caches."""
    skip_dirs = {".venv", "venv", "__pycache__", ".git", "node_modules", ".mypy_cache"}
    symbols: list[Symbol] = []
    for path in Path(repo_path).rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            symbols.extend(extract_symbols(str(path)))
        except SyntaxError:
            continue
    return symbols


def find_symbol_at_line(symbols: list[Symbol], file_path: str, line: int) -> Symbol | None:
    """Return the innermost symbol whose span contains the given line.

    Innermost means: of all matching spans, the one with the smallest range
    (so a method inside a class is preferred over the enclosing class).
    """
    candidates = [
        s for s in symbols
        if _same_file(s.file_path, file_path) and s.line_start <= line <= s.line_end
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.line_end - s.line_start)


def _same_file(a: str, b: str) -> bool:
    return Path(a).name == Path(b).name or Path(a).resolve() == Path(b).resolve()
