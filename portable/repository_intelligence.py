"""Canonical repository-intelligence facade for AUREN.

There is intentionally one repository/code graph store in AUREN: ``CodebaseIndex``
from ``agency_codebase_context``.  This module is the task-facing facade over
that store.  Packing, retrieval, callers/callees, impact, affected tests and
change awareness all consume the same snapshot instead of building competing
indexes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Sequence

from .agency_codebase_context import (
    CodebaseContext,
    CodebaseIndex,
    ContextChunk,
    GraphEdge,
    GraphTrace,
    retrieve,
)

DEFAULT_IGNORES = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "coverage",
})
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]*|\S")


@dataclass(frozen=True)
class PackedFile:
    path: str
    content: str
    token_estimate: int
    compressed: bool = False
    excluded_reason: str = ""


@dataclass(frozen=True)
class RepositoryPack:
    root: str
    snapshot_digest: str
    files: tuple[PackedFile, ...]
    structure: tuple[str, ...]
    token_estimate: int
    omitted: tuple[str, ...]
    security_exclusions: tuple[str, ...]

    def as_text(self) -> str:
        parts = ["<repository>", "<structure>", *self.structure, "</structure>"]
        for item in self.files:
            parts.extend([f'<file path="{item.path}">', item.content, "</file>"])
        parts.append("</repository>")
        return "\n".join(parts)


@dataclass(frozen=True)
class RepositoryEvidence:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    reason: str


@dataclass(frozen=True)
class RepositoryAnswer:
    schema_version: int
    query: str
    snapshot: str
    mode: str
    evidence: tuple[RepositoryEvidence, ...]
    files: tuple[str, ...]
    edges: tuple[GraphEdge, ...]
    unknowns: tuple[str, ...]
    metrics: dict[str, int | float]
    token_estimate: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RepositoryIntelligence:
    """Single entry point for repository context acquisition.

    ``self.index`` is the canonical CodebaseIndex. No other repository graph
    or code map is created by this facade.
    """

    def __init__(self, root: str | Path, *, extra_ignores: Iterable[str] = ()) -> None:
        self.root = Path(root).resolve()
        self.extra_ignores = frozenset(extra_ignores)
        self.index = self._build_index()

    @classmethod
    def build(cls, root: str | Path, *, ignores: Iterable[str] = DEFAULT_IGNORES) -> "RepositoryIntelligence":
        return cls(root, extra_ignores=set(ignores) - set(DEFAULT_IGNORES))

    @property
    def files(self):
        return self.index.files

    @property
    def edges(self):
        return self.index.edges

    def _build_index(self) -> CodebaseIndex:
        return CodebaseIndex.build(self.root, ignores=self.ignore_names())

    def refresh(self) -> None:
        """Refresh the single canonical graph after repository mutations."""
        self.index = self._build_index()

    def ignore_names(self) -> frozenset[str]:
        return DEFAULT_IGNORES | self.extra_ignores

    def digest(self) -> str:
        return self.index.digest()

    def retrieve(self, query: str, *, token_budget: int = 4000, max_files: int = 12,
                 context_lines: int = 20, graph_hops: int = 2) -> CodebaseContext:
        return retrieve(self.index, query, token_budget=token_budget, max_files=max_files,
                        context_lines=context_lines, graph_hops=graph_hops)

    def symbol_matches(self, query: str):
        terms = {x.lower() for x in _terms(query)}
        rows = []
        for record in self.index.files.values():
            for symbol in record.symbols:
                score = sum(8.0 if t == symbol.name.lower() else 4.0 for t in terms if t in symbol.name.lower())
                if score:
                    rows.append((score, symbol))
        return [s for _, s in sorted(rows, key=lambda x: (-x[0], x[1].path, x[1].line))]

    def callers(self, symbol: str) -> tuple[GraphEdge, ...]:
        key = symbol.lower().split(".")[-1]
        return tuple(e for e in self.edges if e.kind == "calls" and e.target_symbol.lower().split(".")[-1] == key)

    def callees(self, symbol: str) -> tuple[GraphEdge, ...]:
        matches = self.symbol_matches(symbol)
        paths = {s.path for s in matches if s.name.lower() == symbol.lower().split(".")[-1]}
        return tuple(e for e in self.edges if e.kind == "calls" and e.source_path in paths)

    def impact(self, symbol: str, depth: int = 2) -> tuple[GraphEdge, ...]:
        seeds = {e.source_path for e in self.callers(symbol)} | {e.target_path for e in self.callers(symbol)}
        seen = set(seeds)
        frontier = set(seeds)
        found: list[GraphEdge] = []
        for _ in range(max(0, min(2, depth))):
            nxt = set()
            for edge in self.edges:
                if edge.source_path not in frontier and edge.target_path not in frontier:
                    continue
                found.append(edge)
                other = edge.target_path if edge.source_path in frontier else edge.source_path
                if other not in seen:
                    seen.add(other)
                    nxt.add(other)
            frontier = nxt
            if not frontier:
                break
        return tuple(dict.fromkeys(found))

    def affected_tests(self, changed: Iterable[str]) -> tuple[str, ...]:
        changed_set = {Path(p).as_posix() for p in changed}
        reverse: dict[str, set[str]] = {}
        for edge in self.edges:
            reverse.setdefault(edge.target_path, set()).add(edge.source_path)
        found = {p for p in changed_set if p in self.files and _is_test(p)}
        queue = list(changed_set)
        seen = set(changed_set)
        while queue:
            current = queue.pop(0)
            for parent in reverse.get(current, ()):
                if parent in seen:
                    continue
                seen.add(parent)
                queue.append(parent)
                if parent in self.files and _is_test(parent):
                    found.add(parent)
        return tuple(sorted(found))

    def changed_files(self, base: str = "HEAD") -> tuple[str, ...]:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", base, "--"],
                cwd=self.root, text=True, capture_output=True, check=False, timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        if proc.returncode != 0:
            return ()
        return tuple(sorted(x.strip().replace("\\", "/") for x in proc.stdout.splitlines() if x.strip()))

    def answer(self, query: str, *, mode: str = "search", token_budget: int = 4000,
               max_files: int = 12, context_lines: int = 24, graph_depth: int = 1,
               base: str = "HEAD") -> RepositoryAnswer:
        context = self.retrieve(query, token_budget=token_budget, max_files=max_files,
                                context_lines=context_lines, graph_hops=graph_depth)
        edges: tuple[GraphEdge, ...] = context.graph_trace.edges
        files = context.relevant_paths
        unknowns = list(context.unknowns)
        if mode == "callers":
            edges = self.callers(query)
        elif mode == "callees":
            edges = self.callees(query)
        elif mode == "impact":
            edges = self.impact(query, graph_depth)
        elif mode == "tests":
            tests = self.affected_tests(context.relevant_paths)
            files = tests
            edges = tuple(e for e in self.edges if e.kind == "tests" and e.source_path in tests)
            if not tests:
                unknowns.append("No affected-test candidate found; this does not prove there are no tests")
        elif mode == "situ":
            changed = self.changed_files(base)
            files = tuple(p for p in changed if p in self.files)
            tests = self.affected_tests(changed)
            files = tuple(dict.fromkeys((*files, *tests)))
            edges = tuple(e for e in self.edges if e.source_path in files or e.target_path in files)
            if not changed:
                unknowns.append("No git diff was available; changed-file state is unknown")
        elif mode == "pack-task":
            pack = self.pack(token_budget=token_budget, compress=True)
            files = tuple(f.path for f in pack.files)
            unknowns.extend(pack.security_exclusions)
        evidence = tuple(RepositoryEvidence(c.path, c.start_line, c.end_line, c.text, c.score, c.reason)
                         for c in context.chunks)
        metrics = {
            "files_indexed": len(self.files),
            "edges": len(self.edges),
            "evidence_items": len(evidence),
            "files_scanned": context.files_scanned,
            "files_read": context.files_read,
        }
        return RepositoryAnswer(1, query, self.digest(), mode, evidence, tuple(files), tuple(edges),
                                tuple(dict.fromkeys(unknowns)), metrics, context.token_estimate)

    def pack(self, *, include: Sequence[str] = (), ignore: Sequence[str] = (),
             token_budget: int | None = None, compress: bool = False,
             max_file_size: int = 50_000_000) -> RepositoryPack:
        patterns = tuple(include)
        ignores = tuple(ignore) + tuple(self._ignore_file_patterns())
        files: list[PackedFile] = []
        structure: list[str] = []
        omitted: list[str] = []
        security: list[str] = []
        used = 0
        candidates = []
        for current, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in self.ignore_names()]
            for name in names:
                path = Path(current) / name
                rel = path.relative_to(self.root).as_posix()
                structure.append(rel)
                if path.is_symlink() or path.stat().st_size > max_file_size:
                    omitted.append(rel); continue
                if patterns and not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(path.name, p) for p in patterns):
                    omitted.append(rel); continue
                if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(path.name, p) for p in ignores):
                    omitted.append(rel); continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    omitted.append(rel); continue
                if self._contains_secret(text):
                    security.append(rel); continue
                candidates.append((rel, text))
        for rel, text in sorted(candidates):
            payload = self._compress(text, rel) if compress else text
            count = self.token_estimate(payload)
            if token_budget is not None and used + count > token_budget:
                omitted.append(rel); continue
            files.append(PackedFile(rel, payload, count, compress)); used += count
        # The pack digest is tied to the canonical graph snapshot as well as content.
        snapshot = hashlib.sha256((self.digest() + "\n" + "\n".join(
            f"{p}|{hashlib.sha256(t.encode()).hexdigest()}" for p, t in candidates)).encode()).hexdigest()
        return RepositoryPack(str(self.root), snapshot, tuple(files), tuple(sorted(structure)), used,
                              tuple(sorted(set(omitted))), tuple(sorted(set(security))))

    def token_estimate(self, text: str) -> int:
        return max(1, len(_TOKEN_RE.findall(text))) if text else 0

    def _ignore_file_patterns(self) -> list[str]:
        patterns: list[str] = []
        for name in (".gitignore", ".ignore", ".repomixignore"):
            path = self.root / name
            if not path.is_file():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("!"):
                        patterns.append(line)
            except OSError:
                continue
        return patterns

    @staticmethod
    def _contains_secret(text: str) -> bool:
        return any(pattern.search(text) for pattern in _SECRET_PATTERNS)

    @staticmethod
    def _compress(text: str, path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".py", ".pyi"}:
            lines = text.splitlines()
            kept: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(("import ", "from ", "class ", "def ", "async def ", "@", "#")):
                    kept.append(line)
            return "\n".join(kept)
        return "\n".join(line for line in text.splitlines() if (
            not line.strip() or line.strip().startswith(("//", "#", "/*", "*", "import ", "using ", "package "))
            or re.search(r"\b(class|interface|struct|enum|function|def|func)\b", line)
        ))


def _terms(query: str) -> tuple[str, ...]:
    return tuple(sorted({x.lower() for x in re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]*", query) if len(x) > 1},
                        key=lambda x: (-len(x), x)))


def _is_test(path: str) -> bool:
    return bool(re.search(r"(^|[._/\\-])(test|tests|spec|specs)([._/\\-]|$)", path, re.I))


def render_compact(answer: RepositoryAnswer) -> str:
    lines = [f"AUREN-REPO-MAP v{answer.schema_version}", f"SNAPSHOT {answer.snapshot}", f"MODE {answer.mode}"]
    for path in answer.files:
        lines.append(f"FILE {path}")
    for edge in answer.edges:
        lines.append(f"EDGE {edge.kind} {edge.source_path} -> {edge.target_path} confidence={edge.confidence:.2f}")
    for item in answer.evidence:
        lines.append(f"EVIDENCE {item.path}:{item.start_line}-{item.end_line} score={item.score:.2f} reason={item.reason}")
    for unknown in answer.unknowns:
        lines.append(f"UNKNOWN {unknown}")
    lines.append(f"TOKENS {answer.token_estimate}")
    return "\n".join(lines)


# Compatibility name: RepositoryMap is not another map implementation. It is
# the canonical task-facing facade backed by the same CodebaseIndex instance.
RepositoryMap = RepositoryIntelligence

__all__ = [
    "PackedFile", "RepositoryPack", "RepositoryEvidence", "RepositoryAnswer",
    "RepositoryIntelligence", "RepositoryMap", "render_compact",
]
