"""Deterministic, IO-aware context selection for AI coding runs.

Inspired by the IO-awareness of FlashAttention and the practical context-engineering patterns in
modern coding-agent harnesses: keep stable context small, retrieve targeted evidence, compact old
history before the model boundary, and preserve correctness-critical state.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

from runtime.context_cache import ContextPageCache

_REPOSITORY_FILE_DIGEST_LIMIT_BYTES = 2 * 1024 * 1024
_REPOSITORY_DIGEST_BUDGET_BYTES = 32 * 1024 * 1024

_STOP = {"the", "and", "for", "with", "from", "this", "that", "into", "have", "will", "task", "change", "please", "need", "make", "using"}
_PRIORITY = (
    ("traceback", 8.0),
    ("exception", 7.5),
    ("denied", 7.5),
    ("error", 7.0),
    ("security", 7.0),
    ("blocked", 6.5),
    ("timeout", 6.5),
    ("cancelled", 6.5),
    ("critical", 6.0),
    ("acceptance", 6.0),
    ("constraint", 5.0),
    ("protected", 5.0),
    ("decision", 5.0),
    ("evidence", 5.0),
    ("verification", 5.0),
    ("regression", 5.0),
    ("root cause", 4.5),
    ("missing", 4.5),
    ("risk", 4.5),
    ("failed", 4.5),
    ("warning", 4.0),
    ("next", 3.5),
    ("changed", 3.0),
    ("file", 1.0),
)
_SIGNAL_MARKERS = (
    "traceback", "exception", "denied", "error", "security", "blocked",
    "timeout", "cancelled", "critical", "acceptance", "constraint",
    "protected", "decision", "evidence", "verification", "regression",
    "root cause", "missing", "risk", "failed", "warning",
)

# Process-local cache. Provider-side KV caching is intentionally left to the
# provider adapter because CLI providers expose different caching contracts.
_PAGE_CACHE = ContextPageCache(page_chars=1800)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()) if w not in _STOP}


def _rank_file(name: str, task_words: set[str]) -> float:
    words = _words(name.replace("/", " "))
    score = len(words & task_words) * 5
    lowered = name.lower()
    for marker, weight in (("test", 1.0), ("readme", 0.5), ("docs", 0.5), ("config", 1.5), ("src/", 1.0), ("lib/", 1.0)):
        if marker in lowered:
            score += weight
    return score


def _fingerprint_repository_file(
    root: Path, name: str, remaining_bytes: int,
) -> tuple[str | None, str | None, int]:
    """Return a content digest when safe and bounded, otherwise a visible warning."""
    try:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            return None, "path-escape", 0
        root_resolved = root.resolve()
        path = (root_resolved / relative).resolve(strict=True)
        path.relative_to(root_resolved)
        before = path.stat()
        if not path.is_file():
            return None, "not-a-file", 0
        if before.st_size > _REPOSITORY_FILE_DIGEST_LIMIT_BYTES:
            return None, f"file-size-limit;size={before.st_size};mtime_ns={before.st_mtime_ns}", 0
        if before.st_size > remaining_bytes:
            return None, f"snapshot-budget;size={before.st_size};mtime_ns={before.st_mtime_ns}", 0
        content = path.read_bytes()
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            return None, f"changed-during-read;size={after.st_size};mtime_ns={after.st_mtime_ns}", 0
        return hashlib.sha256(content).hexdigest()[:12], None, len(content)
    except (OSError, RuntimeError, ValueError):
        return None, "unreadable-or-outside-root", 0


def build_repository_context(root: Path, task: str, limit_files: int = 180, budget_chars: int = 9000) -> str:
    try:
        result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root, text=True, capture_output=True, check=False, timeout=30)
        if result.returncode != 0:
            return "# Repository Context\nUnavailable: git ls-files failed."
        files = [line for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return "# Repository Context\nUnavailable: repository enumeration failed."

    task_words = _words(task)
    ranked = sorted(files, key=lambda item: (-_rank_file(item, task_words), item))
    selected = ranked[:limit_files]

    top_level: dict[str, list[str]] = {}
    for name in selected:
        parts = Path(name).parts
        bucket = parts[0] if parts else "."
        top_level.setdefault(bucket, []).append(name)

    lines = ["# Repository Context", "", "## Stable structure"]
    used = 0
    omitted_buckets = 0
    status_reserve = 220
    for index, bucket in enumerate(sorted(top_level)):
        entry = f"- {bucket}/ ({len(top_level[bucket])} relevant files)"
        if used + len(entry) + 1 + status_reserve > budget_chars:
            omitted_buckets = len(top_level) - index
            break
        lines.append(entry)
        used += len(entry) + 1

    lines.extend(["", "## Relevant files"])
    fingerprinted = 0
    fingerprint_warnings = 0
    omitted_files = 0
    bytes_hashed = 0
    root_path = Path(root)
    for index, name in enumerate(selected):
        digest, warning, bytes_used = _fingerprint_repository_file(
            root_path, name, _REPOSITORY_DIGEST_BUDGET_BYTES - bytes_hashed,
        )
        bytes_hashed += bytes_used
        if digest:
            fingerprinted += 1
            fingerprint = f"sha256:{digest}"
        else:
            fingerprint_warnings += 1
            fingerprint = f"digest-unavailable:{warning or 'unknown'}"
        entry = f"- {name} [{fingerprint}]"
        if used + len(entry) + 1 + status_reserve > budget_chars:
            omitted_files = len(selected) - index
            fingerprint_warnings += omitted_files
            break
        lines.append(entry)
        used += len(entry) + 1

    complete = fingerprint_warnings == 0 and omitted_buckets == 0 and omitted_files == 0
    status = (
        f"- selected-file fingerprints complete: {'yes' if complete else 'no'}; "
        f"hashed={fingerprinted}/{len(selected)}; warnings={fingerprint_warnings}; "
        f"omitted_files={omitted_files}; omitted_buckets={omitted_buckets}; bytes_hashed={bytes_hashed}"
    )
    lines.extend(["", "## Fingerprint status", status])
    return "\n".join(lines) + "\n"


def compact_history(history: str, task: str, budget_chars: int) -> str:
    """Compress history by information value, retaining proof and task-critical state first.

    This is deliberately deterministic and model-free. It does not invent summaries. It removes
    repeated low-value lines and keeps the highest-value evidence, with a small recent tail so the
    current execution state remains visible. That gives the caller a predictable token ceiling.
    """
    if budget_chars <= 0 or not history.strip():
        return ""
    lines = [line.strip() for line in history.splitlines() if line.strip()]
    task_words = _words(task)
    candidates: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        normalized = re.sub(r"\s+", " ", line.lower())
        if normalized in seen:
            continue
        seen.add(normalized)
        score = len(_words(line) & task_words) * 2.0
        for marker, weight in _PRIORITY:
            if marker in normalized:
                score += weight
        score += index / max(1, len(lines))
        candidates.append((score, index, line))

    candidates.sort(key=lambda item: (-item[0], -item[1]))
    selected: list[tuple[int, str]] = []
    used = 0
    for _, index, line in candidates:
        entry_size = len(line) + 1
        if used + entry_size > budget_chars:
            continue
        selected.append((index, line))
        used += entry_size

    if lines:
        recent = lines[-1]
        if recent not in {line for _, line in selected} and used + len(recent) + 1 <= budget_chars:
            selected.append((len(lines) - 1, recent))
    selected.sort(key=lambda item: item[0])
    return "\n".join(line for _, line in selected)


def _paged_section(
    kind: str, text: str, budget_chars: int, *, stable: bool = False, replace: bool = False,
) -> tuple[str, list[str]]:
    pages = _PAGE_CACHE.paginate(kind, text, stable=stable, replace=replace)
    selected = _PAGE_CACHE.select(pages, budget_chars)
    return "\n".join(page.text for page in selected), [page.page_id for page in selected]


def select_context(task: str, repo_map: str, memory: str, history: str, budget_chars: int = 12000) -> dict[str, Any]:
    """Build compact context tiles using relevance ranking plus reusable pages."""
    task_words = _words(task)

    def rank_lines(text: str) -> list[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return sorted(lines, key=lambda line: (-len(_words(line) & task_words), -len(line)))

    memory_lines = rank_lines(memory)
    history_budget = max(1200, budget_chars // 5)
    compressed_history = compact_history(history, task, history_budget)
    history_lines = rank_lines(compressed_history)
    remaining = budget_chars

    repository, repository_pages = _paged_section(
        "repository", repo_map[: min(len(repo_map), remaining // 2)], min(remaining // 2, 9000), stable=True, replace=True
    )
    remaining -= len(repository)
    memory_text, memory_pages = _paged_section(
        "memory", "\n".join(memory_lines), min(max(1200, budget_chars // 6), remaining)
    )
    remaining -= len(memory_text)
    history_text, history_pages = _paged_section(
        "history", "\n".join(history_lines), min(history_budget, remaining)
    )

    output = {
        "repository": repository or "None.",
        "memory": memory_text or "None.",
        "history": history_text or "None.",
        "budget_chars": budget_chars,
        "selected": {
            "repository": len(repository),
            "memory": len(memory_text),
            "history": len(history_text),
        },
        "pages": {
            "repository": repository_pages,
            "memory": memory_pages,
            "history": history_pages,
        },
        "cache": _PAGE_CACHE.stats(),
        "history_compression": {
            "input_chars": len(history),
            "output_chars": len(history_text),
            "ratio": round(len(history_text) / max(1, len(history)), 4),
            "approx_input_tokens": (len(history) + 3) // 4,
            "approx_output_tokens": (len(history_text) + 3) // 4,
            "token_estimate_method": "characters-divided-by-four; not provider token usage",
            "input_signals": sorted(
                marker for marker in _SIGNAL_MARKERS
                if re.search(r"\b" + re.escape(marker) + r"\b", history, re.IGNORECASE)
            ),
            "retained_signals": sorted(
                marker for marker in _SIGNAL_MARKERS
                if re.search(r"\b" + re.escape(marker) + r"\b", history, re.IGNORECASE)
                and re.search(r"\b" + re.escape(marker) + r"\b", history_text, re.IGNORECASE)
            ),
            "missed_signals": sorted(
                marker for marker in _SIGNAL_MARKERS
                if re.search(r"\b" + re.escape(marker) + r"\b", history, re.IGNORECASE)
                and not re.search(r"\b" + re.escape(marker) + r"\b", history_text, re.IGNORECASE)
            ),
        },
        "strategy": "stable-prefix + content-addressed-page-cache + evidence-priority-compaction + tiled-context + relevance-ranking + bounded-evidence",
    }
    return output



def benchmark_context_selection(
    task: str,
    repo_map: str,
    memory: str,
    history: str,
    budget_chars: int = 12000,
    *,
    provider_token_usage: dict[str, int] | None = None,
    task_success: bool | None = None,
    evaluation_ref: str | None = None,
) -> dict[str, Any]:
    """Measure context economics and attach evaluator-supplied success evidence when available."""
    if task_success is not None and type(task_success) is not bool:
        raise ValueError("task success must be a boolean or None")
    if task_success is not None and (
        not isinstance(evaluation_ref, str) or not evaluation_ref.strip() or len(evaluation_ref) > 512
    ):
        raise ValueError("task success requires a bounded evaluation reference")
    if task_success is None and evaluation_ref is not None:
        raise ValueError("evaluation reference requires a task success result")
    started = perf_counter()
    selected = select_context(task, repo_map, memory, history, budget_chars)
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    raw_parts = (repo_map, memory, history)
    bounded_parts = tuple(selected[key] for key in ("repository", "memory", "history"))
    raw_chars = sum(map(len, raw_parts))
    bounded_chars = sum(map(len, bounded_parts))
    raw_digest = hashlib.sha256("\0".join(raw_parts).encode("utf-8")).hexdigest()
    bounded_digest = hashlib.sha256("\0".join(bounded_parts).encode("utf-8")).hexdigest()
    usage: dict[str, int] | None = None
    if provider_token_usage is not None:
        if not isinstance(provider_token_usage, dict):
            raise ValueError("provider token usage must be a mapping")
        usage = {}
        for key in ("input_tokens", "output_tokens"):
            value = provider_token_usage.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("provider token usage must contain non-negative integer token counts")
            usage[key] = value
    return {
        "raw_chars": raw_chars,
        "bounded_chars": bounded_chars,
        "raw_sha256": raw_digest,
        "bounded_sha256": bounded_digest,
        "reduction_ratio": round(1 - bounded_chars / max(1, raw_chars), 4),
        "approx_raw_tokens": (raw_chars + 3) // 4,
        "approx_bounded_tokens": (bounded_chars + 3) // 4,
        "token_estimate_method": "characters-divided-by-four; not provider token usage",
        "provider_token_usage": usage,
        "task_success": task_success,
        "evaluation_ref": evaluation_ref,
        "task_success_source": "host-evaluator" if task_success is not None else None,
        "latency_ms": elapsed_ms,
        "history_compression": selected["history_compression"],
        "retained_signals": selected["history_compression"]["retained_signals"],
        "missed_signals": selected["history_compression"]["missed_signals"],
        "budget_chars": budget_chars,
    }


def flash_context_prompt(task: str, repo_map: str, memory: str, history: str, budget_chars: int = 12000) -> tuple[str, str, str, str]:
    selected = select_context(task, repo_map, memory, history, budget_chars)
    metadata = json.dumps(
        {
            "strategy": selected["strategy"],
            "budget_chars": budget_chars,
            "selected_chars": selected["selected"],
            "pages": selected["pages"],
            "cache": selected["cache"],
            "history_compression": selected["history_compression"],
            "provider_kv_cache": "adapter-dependent; not assumed",
        },
        ensure_ascii=False,
    )
    return selected["repository"], selected["memory"], selected["history"], metadata
