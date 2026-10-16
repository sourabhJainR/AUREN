"""Deterministic engineering-design gate distilled from classic software-design guidance.

The guard does not attempt to judge code with heuristics or reproduce any book.
It checks whether a task has declared the design facts that matter for its risk:
ownership, boundaries, data semantics, failure behavior, compatibility, and
verification. It produces a compact receipt that can be attached to AUREN evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping


class DesignDimension(str, Enum):
    COMPLEXITY = "complexity"
    ARCHITECTURE = "architecture"
    DOMAIN = "domain"
    DATA = "data"
    RESILIENCE = "resilience"
    REFACTORING = "refactoring"
    LEGACY = "legacy"
    CONSTRUCTION = "construction"
    COMPATIBILITY = "compatibility"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONCURRENCY = "concurrency"
    DISTRIBUTED = "distributed"
    CORRECTNESS = "correctness"
    OPERATIONS = "operations"
    AI_AGENT = "ai_agent"


PRINCIPLE_FAMILY_IDS = (
    "fundamental_design",
    "security_privacy",
    "performance_cost",
    "concurrency",
    "distributed_systems",
    "reliability_recovery",
    "data_correctness",
    "api_integration",
    "testing_verification",
    "delivery_operations",
    "observability_governance",
    "ai_autonomous_systems",
)


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class DesignFinding:
    dimension: DesignDimension
    severity: FindingSeverity
    message: str
    evidence_key: str | None = None


@dataclass(frozen=True)
class DesignReviewReceipt:
    intent_digest: str
    dimensions: tuple[str, ...]
    declared: Mapping[str, str]
    findings: tuple[DesignFinding, ...]
    status: str
    receipt_digest: str
    changed_paths: tuple[str, ...] = ()
    risk: str = "medium"
    code_revision: str = ""

    @property
    def blocking(self) -> bool:
        return any(f.severity == FindingSeverity.BLOCKING for f in self.findings)


@dataclass(frozen=True)
class RepositoryDesignRecommendation:  # pragma: no cover - passive DTO; behavior is covered through the guard
    status: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    versions: tuple[str, ...]
    algorithm: str
    workflow: tuple[str, ...]
    patterns: tuple[str, ...]
    rationale: tuple[str, ...]
    alternatives: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    reuse_candidates: tuple[str, ...]
    verification: tuple[str, ...]
    rollback: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    warnings: tuple[str, ...]


class EngineeringDesignGuard:
    """Infer and validate the minimum design contract for a change.

    ``design`` is a small mapping supplied by the planner/agent. Values should
    state facts, decisions, or explicit ``not_applicable: reason`` declarations.
    The guard never treats model prose as proof; it only checks that the required
    contract was declared so later verification/review can test it.

    The guard is also the single home for curated-change quality evidence. It
    deliberately extends the existing design contract instead of creating a
    second quality gate or workflow abstraction.
    """

    _DIMENSION_KEYS = {
        DesignDimension.COMPLEXITY: ("complexity", "module", "abstraction"),
        DesignDimension.ARCHITECTURE: ("architecture", "boundary", "dependency"),
        DesignDimension.DOMAIN: ("domain", "bounded_context", "aggregate", "invariant"),
        DesignDimension.DATA: (
            "data", "source_of_truth", "consistency", "idempotency", "ordering",
            "db_access", "query_pattern", "n_plus_one", "schema_evolution",
            "migration", "backfill", "reconciliation", "transaction",
        ),
        DesignDimension.RESILIENCE: (
            "resilience", "timeout", "retry", "recovery", "observability", "performance",
        ),
        DesignDimension.REFACTORING: ("refactoring", "behavior_delta", "safety_net", "smell"),
        DesignDimension.LEGACY: ("legacy", "characterization", "seam", "dependency_break"),
        DesignDimension.CONSTRUCTION: (
            "construction", "validation", "error_handling", "test",
            "exception_handling", "logging", "regression", "reuse",
        ),
        DesignDimension.COMPATIBILITY: (
            "compatibility", "rollout", "migration", "contract", "usage_compatibility",
        ),
        DesignDimension.SECURITY: ("security", "threat_model", "authorization", "least_privilege", "input_validation", "secrets", "privacy", "tool_permissions"),
        DesignDimension.PERFORMANCE: ("performance", "benchmark", "performance_budget", "latency", "resource_budget", "profiling"),
        DesignDimension.CONCURRENCY: ("concurrency", "ownership", "synchronization", "cancellation", "deadlock", "race_test", "parallelism"),
        DesignDimension.DISTRIBUTED: ("distributed", "consistency", "cap_tradeoff", "pacelc", "idempotency", "ordering", "quorum", "split_brain"),
        DesignDimension.CORRECTNESS: ("correctness", "invariant", "transaction", "isolation", "migration", "source_of_truth", "reconciliation"),
        DesignDimension.OPERATIONS: ("operations", "observability", "rollback", "recovery", "slo", "rto_rpo", "rollout"),
        DesignDimension.AI_AGENT: ("ai_agent", "agent_safety", "tool_permissions", "human_approval", "evaluation", "provenance", "autonomy_budget", "prompt_injection"),
    }

    _PATH_RULES = (
        (re.compile(r"(^|/)(domain|domains|aggregate|entity|value_object)(/|$)|bounded[_-]?context", re.I), {DesignDimension.DOMAIN}),
        (re.compile(r"(^|/)(db|database|storage|repository|migration|schema|cache|queue|event|stream|consumer|producer)(/|$)|(^|_)(sql|cdc|replica|projection)(_|$)", re.I), {DesignDimension.DATA}),
        (re.compile(r"(^|/)(api|client|gateway|adapter|integration|http|grpc|worker|job|queue|deploy|infra|ops)(/|$)|timeout|retry|circuit|health", re.I), {DesignDimension.RESILIENCE, DesignDimension.ARCHITECTURE}),
        (re.compile(r"test|spec|fixture|mock|stub|refactor", re.I), {DesignDimension.CONSTRUCTION, DesignDimension.REFACTORING}),
        (re.compile(r"legacy|compat|migration|upgrade|deprecat", re.I), {DesignDimension.LEGACY, DesignDimension.COMPATIBILITY}),
        (re.compile(r"(^|/)(controller|handler|service|use[_-]?case|application)(/|$)", re.I), {DesignDimension.ARCHITECTURE, DesignDimension.CONSTRUCTION}),
        (re.compile(r"(^|/)(log|logging|telemetry|observability|diagnostic)(/|$)|\blog(ging)?\b", re.I), {DesignDimension.CONSTRUCTION, DesignDimension.RESILIENCE}),
        (re.compile(r"(^|/)(exception|error|errors|fault)(/|$)|\b(exception|error handling|failure path)\b", re.I), {DesignDimension.CONSTRUCTION, DesignDimension.RESILIENCE}),
        (re.compile(r"(^|/)(benchmark|performance|profil|latency|throughput)(/|$)|\b(performance|latency|throughput|allocation|hot path)\b", re.I), {DesignDimension.RESILIENCE}),
        (re.compile(r"(^|/)(benchmark|performance|profil|latency|throughput)(/|$)|\b(performance|latency|throughput|allocation|hot path)\b", re.I), {DesignDimension.RESILIENCE, DesignDimension.PERFORMANCE}),
        (re.compile(r"(^|/)(auth|authn|authz|security|secrets|crypto|permission|policy|sandbox)(/|$)|\b(security|privacy|authorization|authentication|secret|prompt injection|tool permission|threat model)\b", re.I), {DesignDimension.SECURITY}),
        (re.compile(r"(^|/)(async|concurrency|thread|lock|worker|scheduler|parallel)(/|$)|\b(concurrency|race conditions?|deadlock|cancellation|parallelism|shared state|thread safe)\b", re.I), {DesignDimension.CONCURRENCY}),
        (re.compile(r"(^|/)(distributed|replica|cluster|consensus|quorum|consumer|producer|stream|queue|event)(/|$)|\b(cap theorem|pacelc|partition tolerance|eventual consistency|split brain|distributed system|idempotent consumer)\b", re.I), {DesignDimension.DISTRIBUTED}),
        (re.compile(r"(^|/)(transaction|migration|schema|integrity|invariant|reconcile)(/|$)|\b(data integrity|transaction boundary|schema migration|invariant|serializability)\b", re.I), {DesignDimension.CORRECTNESS}),
        (re.compile(r"(^|/)(deploy|release|ops|runbook|recovery|backup|restore)(/|$)|\b(rollback|disaster recovery|rto|rpo|slo|error budget)\b", re.I), {DesignDimension.OPERATIONS}),
        (re.compile(r"(^|/)(agent|agents|arena|model|prompt|tool_runner|memory|learning)(/|$)|\b(ai agent|autonomous|prompt injection|model evaluation|tool execution|agent memory)\b", re.I), {DesignDimension.AI_AGENT, DesignDimension.SECURITY}),
    )

    _HIGH_RISK = {"high", "critical"}
    _CODE_SUFFIXES = {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".cs", ".fs",
        ".vb", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".swift",
        ".rb", ".php", ".ex", ".exs", ".dart", ".lua", ".sh", ".ps1",
    }

    _UNIVERSAL_QUALITY = (
        ("reuse", DesignDimension.CONSTRUCTION, "Declare existing implementations inspected, what was reused/adapted, or an explicit not_applicable reason"),
        ("regression", DesignDimension.CONSTRUCTION, "Declare the regression safety net and affected existing behavior to verify, or an explicit not_applicable reason"),
        ("exception_handling", DesignDimension.CONSTRUCTION, "Declare repository-native exception propagation/translation and cleanup behavior, or an explicit not_applicable reason"),
        ("logging", DesignDimension.CONSTRUCTION, "Declare repository-native logging/telemetry conventions and relevant diagnostic context, or an explicit not_applicable reason"),
        ("performance", DesignDimension.RESILIENCE, "Declare expected performance/resource behavior and avoided hot-path overhead, or an explicit not_applicable reason"),
        ("usage_compatibility", DesignDimension.COMPATIBILITY, "Declare how existing APIs, callers, workflow ordering, and usage patterns remain compatible, or an explicit not_applicable reason"),
    )

    @staticmethod
    def intent_digest(intent: str) -> str:
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("engineering design intent must be non-empty text")
        return hashlib.sha256(intent.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _repository_stack(root: Path) -> dict[str, object]:
        """Detect only stack facts backed by repository manifests or CI."""
        root = Path(root)
        manifest_names = (
            "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt",
            "package.json", "Cargo.toml", "go.mod", "pom.xml",
        )
        manifest_text: dict[str, str] = {}
        for name in manifest_names:
            path = root / name
            if path.is_file():
                try:
                    manifest_text[name] = path.read_text(encoding="utf-8")[:200_000]
                except OSError:
                    continue
        csproj_paths = list(root.glob("*.csproj"))[:10]
        for path in csproj_paths:
            try:
                manifest_text[path.name] = path.read_text(encoding="utf-8")[:100_000]
            except OSError:
                continue

        languages: list[str] = []
        versions: list[str] = []
        evidence: list[str] = []
        if any(name in manifest_text for name in ("pyproject.toml", "setup.cfg", "setup.py", "requirements.txt")) or (root / "portable").is_dir():
            languages.append("Python")
            evidence.extend(name for name in ("pyproject.toml", "setup.cfg", "setup.py", "requirements.txt") if name in manifest_text)
            pyproject = manifest_text.get("pyproject.toml", "")
            version_match = re.search(r"(?im)^\s*requires-python\s*=\s*['\"]([^'\"]+)", pyproject)
            if version_match:
                versions.append("Python requires-python " + version_match.group(1))
            for workflow in list((root / ".github" / "workflows").glob("*.yml"))[:20] + list((root / ".github" / "workflows").glob("*.yaml"))[:20] if (root / ".github" / "workflows").is_dir() else []:
                try:
                    text = workflow.read_text(encoding="utf-8")[:100_000]
                except OSError:
                    continue
                matrix_lines = re.findall(r"(?m)^\s*python-version:\s*([^\n]+)", text)
                matrix = sorted(set(re.findall(r"([0-9]+\.[0-9]+)", "\n".join(matrix_lines))))
                if matrix:
                    versions.append("CI tests Python " + ", ".join(matrix))
                    evidence.append(workflow.relative_to(root).as_posix())
            if (root / "scripts" / "run_full_suite.py").is_file():
                suite = (root / "scripts" / "run_full_suite.py").read_text(encoding="utf-8")[:30_000]
                if "unittest" in suite and "discover" in suite:
                    versions.append("test runner unittest discover")
                    evidence.append("scripts/run_full_suite.py")
                elif "pytest" in suite:
                    versions.append("test runner pytest")
                    evidence.append("scripts/run_full_suite.py")
        if "package.json" in manifest_text:
            languages.append("JavaScript/TypeScript")
            evidence.append("package.json")
            try:
                package = json.loads(manifest_text["package.json"])
                node = package.get("engines", {}).get("node")
                if node:
                    versions.append("Node.js " + str(node))
                deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
                versions.extend(f"{name} {deps[name]}" for name in ("react", "next", "vue", "angular", "express") if name in deps)
            except (ValueError, AttributeError, TypeError):
                versions.append("package.json present but unreadable; framework unresolved")
        if "Cargo.toml" in manifest_text:
            languages.append("Rust")
            evidence.append("Cargo.toml")
            match = re.search(r"(?m)^\s*rust-version\s*=\s*['\"]([^'\"]+)", manifest_text["Cargo.toml"])
            if match:
                versions.append("Rust " + match.group(1))
        if "go.mod" in manifest_text:
            languages.append("Go")
            evidence.append("go.mod")
            match = re.search(r"(?m)^go\s+([0-9.]+)", manifest_text["go.mod"])
            if match:
                versions.append("Go " + match.group(1))
        if "pom.xml" in manifest_text:
            languages.append("Java")
            evidence.append("pom.xml")
            match = re.search(r"<maven\.compiler\.(?:release|source)>\s*([^<]+)", manifest_text["pom.xml"])
            if match:
                versions.append("Java compiler level " + match.group(1).strip())
        if csproj_paths:
            languages.append("C#/.NET")
            evidence.extend(path.name for path in csproj_paths)
            for name, text in manifest_text.items():
                if name.endswith(".csproj"):
                    match = re.search(r"<TargetFrameworks?>\s*([^<]+)", text)
                    if match:
                        versions.append(".NET target " + match.group(1).strip())
        return {
            "languages": tuple(dict.fromkeys(languages)),
            "versions": tuple(dict.fromkeys(versions)),
            "evidence": tuple(dict.fromkeys(evidence)),
            "frameworks": tuple(
                name for name in ("Django", "FastAPI", "Flask", "React", "Next.js", "Vue", "Angular", "Express")
                if any(token.casefold() in text.casefold() for text in manifest_text.values()
                       for token in ({
                           "Django": ("django",), "FastAPI": ("fastapi",), "Flask": ("flask",),
                           "React": ("react",), "Next.js": ("next",), "Vue": ("vue",),
                           "Angular": ("@angular", "angular"), "Express": ("express",),
                       }[name]))
            ),
        }

    @classmethod
    def recommend_approaches(
        cls, root: Path | str, intent: str, changed_paths: Iterable[str] = (),
    ) -> dict[str, object]:
        """Return bounded, evidence-linked approach options for the detected repository stack.

        Recommendations are deterministic suggestions, not proof or an execution
        decision. Unknown stack versions stay unknown; callers still own approval,
        verification, security policy and promotion gates.
        """
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("recommendation intent must be non-empty text")
        root = Path(root).resolve()
        paths = tuple(str(path) for path in changed_paths)
        profile = cls._repository_stack(root)
        stack_known = bool(profile["languages"])
        text = (intent + " " + " ".join(paths)).casefold()
        evidence = list(profile["evidence"])
        recommendations: list[dict[str, object]] = []

        def add(pattern: str, approach: str, rationale: str, tradeoffs: str,
                alternatives: tuple[str, ...] = (), proof: tuple[str, ...] = ()) -> None:
            recommendations.append({
                "pattern": pattern, "approach": approach, "rationale": rationale,
                "tradeoffs": tradeoffs, "alternatives": alternatives,
                "repository_evidence": tuple(dict.fromkeys((*evidence, *proof))),
                "stack": profile, "requires_stack_confirmation": not stack_known,
            })

        if any(word in text for word in ("memory", "context", "token", "cache", "compaction")):
            reuse = tuple(path for path in (
                "portable/memory_orchestrator.py", "portable/persistent_memory.py",
                "portable/context_graph.py", "portable/context_engine.py",
                ".ai-harness/runtime/context_cache.py", ".ai-harness/runtime/auto_compaction.py",
            ) if (root / path).is_file())
            if reuse:
                add("bounded retrieval + content-addressed cache",
                    "Reuse the existing memory/context owners; rank scoped evidence before packing, keep required contracts intact, and fail closed when they do not fit.",
                    "The repository already has canonical durable memory, provenance graph, context packing, and cache primitives.",
                    "Improves reuse and bounds memory movement; ranking adds small CPU work and can omit low-ranked optional context.",
                    ("Create a new store/cache only if measured limits of the canonical owners are demonstrated.",), reuse)
            else:
                add("bounded retrieval + explicit lifetime",
                    "Separate durable evidence from task-local working state; add TTL only to disposable observations and preserve required context.",
                    "The task concerns context/memory but no canonical memory owner was found in the scanned repository.",
                    "Bounds context and resource use; durable state requires schema and migration ownership.",
                    ("Confirm the stack and existing storage owner before implementation.",))

        if any(word in text for word in ("quota", "rate limit", "retry", "resume", "provider reset", "token exhausted")):
            reuse = tuple(path for path in (
                "portable/execution_controller.py", "portable/autonomous_daemon.py",
                ".ai-harness/TOKEN_POLICY.md", ".ai-harness/runtime/auto_compaction.py",
            ) if (root / path).is_file())
            add("durable state machine + deadline scheduling",
                "Persist waiting state and provider reset deadline; honor Retry-After, use bounded backoff only when reset is unknown, and sleep locally without provider polling.",
                "Avoids busy loops, repeated side effects and token consumption while blocked.",
                "Adds durable state transitions and restart checks; reset metadata must be preserved by the provider adapter.",
                ("Use provider switching only when explicitly authorized and policy-equivalent.",), reuse)

        if any(word in text for word in ("test", "coverage", "regression", "bug", "refactor")):
            if "Python" in profile["languages"] and any("unittest" in str(x) for x in profile["versions"]):
                test_runner = "Use repository unittest discovery and behavior-focused regression tests."
            elif "Python" in profile["languages"] and any("pytest" in str(x).lower() for x in profile["versions"]):
                test_runner = "Use the repository's detected pytest runner and behavior-focused regression tests."
            else:
                test_runner = "Reuse the repository's detected test runner; if no runner is evidenced, ask before adding a dependency."
            add("characterization-first incremental change", test_runner,
                "Existing tests and conventions reduce false positives and avoid adding a competing test framework.",
                "Fast focused feedback; broader integration tests still needed for lifecycle and failure semantics.",
                ("For passive Python DTO dataclasses, use class-level # pragma: no cover only when supported by repo convention.",),
                tuple(path for path in ("tests", "scripts/run_full_suite.py") if (root / path).exists()))

        if any(word in text for word in ("database", "persist", "store", "migration", "query")):
            reuse = tuple(path for path in (
                "portable/persistent_memory.py", "portable/context_graph.py",
                ".ai-harness/runtime/task_memory.py",
            ) if (root / path).is_file())
            add("single source of truth + transactional persistence",
                "Extend the canonical repository store, use idempotent writes and explicit transaction boundaries, and reuse existing indexes/FTS before introducing a second store.",
                "Reduces split-brain state, duplicate migrations and inconsistent retention.",
                "Central ownership simplifies recovery but requires respecting the existing schema, locks and data budget.",
                ("Add a separate store only when isolation, scale or workload evidence requires it.",), reuse)

        if any(word in text for word in ("concurrent", "parallel", "async", "race", "deadlock", "background")):
            reuse = tuple(path for path in (
                "portable/execution_controller.py", "portable/autonomous_daemon.py",
                "portable/agent_memory.py",
            ) if (root / path).is_file())
            add("bounded concurrency + explicit ownership",
                "Reuse existing leases, locks and supervisor lifecycle; define cancellation, idempotency and a maximum in-flight budget before adding workers.",
                "Prevents duplicate execution, unbounded resource use and hidden scheduler loops.",
                "Bounded throughput is predictable; more serialization may reduce peak parallelism.",
                ("Use message passing when shared mutable state cannot be made safe.",), reuse)

        if any(word in text for word in ("deduplicate", "dedup", "unique ids", "membership lookup", "duplicate ids")):
            add("single-pass hash-based deduplication",
                "Use a set for membership and a dict keyed by stable identity to deduplicate in one pass; preserve input order explicitly when callers rely on it.",
                "Avoids nested membership scans and typically reduces O(n²) comparison work to O(n) expected time.",
                "Uses O(n) auxiliary memory and requires a trustworthy stable key; sort-based approaches may be preferable when ordering is the primary requirement.",
                ("Use sort-and-scan when a deterministic total order is required and O(n log n) is acceptable.",),
                tuple(path for path in ("portable/agent_capabilities.py", "portable/context_graph.py") if (root / path).is_file()))

        if any(word in text for word in ("top-k", "rank", "ranking", "retrieval", "search results")):
            add("indexed retrieval + bounded selection",
                "Reuse the existing FTS/indexed query path; use a bounded heap for top-k when k is small, or a full deterministic sort when callers need a complete ranking.",
                "Reduces irrelevant context and can avoid sorting the entire candidate set for small top-k requests.",
                "Heap selection is O(n log k) but has more implementation complexity; full sort is simpler and gives a complete stable order.",
                ("Use database ranking/indexes when the canonical store already supports the query.",),
                tuple(path for path in ("portable/agent_capabilities.py", "portable/context_engine.py", "portable/context_graph.py") if (root / path).is_file()))

        if any(word in text for word in ("dependency graph", "topological", "workflow order", "cycle detection", "dag")):
            add("DAG validation + topological scheduling",
                "Represent dependencies as adjacency lists, detect cycles before execution, and topologically order only ready work; reuse the existing state/task graph where present.",
                "Makes dependency order explicit and isolates blocked tasks without spinning independent work.",
                "Runs in O(V+E) time; requires cycle diagnostics and clear semantics for optional versus hard dependencies.",
                ("Use a state machine for sequential lifecycles; use a DAG only when independent branches or dependency edges are real.",),
                tuple(path for path in ("portable/agency_state_graph.py", "portable/execution_controller.py", "portable/task_planner.py") if (root / path).is_file()))

        if any(word in text for word in ("large file", "large log", "streaming", "unbounded input", "memory footprint")):
            add("streaming + bounded working set",
                "Process records incrementally and cap retained state; persist checkpoints at committed boundaries instead of materializing unbounded input.",
                "Bounds peak memory and improves restart recovery on large inputs.",
                "Streaming complicates global ordering and rollback; checkpoint cadence trades write overhead against replay cost.",
                ("Use chunked batch processing when downstream operations require bounded groups.",),
                tuple(path for path in (".ai-harness/runtime/auto_compaction.py", "portable/execution_controller.py") if (root / path).is_file()))

        if not recommendations:
            add("smallest repository-native change",
                "Inspect nearby implementations, language/framework versions, test conventions and callers; extend the existing owner and verify the exact changed paths.",
                "No specific algorithm/pattern trigger was evidenced, so avoid imposing architecture by decoration.",
                "Keeps scope and migration risk low; deeper profiling may be needed if performance is the actual constraint.",
                ("Ask for clarification if the task depends on an unknown framework/version or a material design choice.",))
        return {
            "stack": profile,
            "needs_stack_confirmation": not stack_known,
            "recommendations": recommendations[:4],
        }

    @classmethod
    def _is_code_change(cls, paths: tuple[str, ...]) -> bool:
        for path in paths:
            lower = path.lower()
            if lower.startswith(("docs/", "doc/", "documentation/")):
                continue
            if any(lower.endswith(suffix) for suffix in cls._CODE_SUFFIXES):
                return True
        return False

    @classmethod
    def infer_dimensions(cls, intent: str, changed_paths: Iterable[str], risk: str = "medium") -> tuple[DesignDimension, ...]:
        text = f"{intent} {' '.join(changed_paths)}".lower()
        dimensions: set[DesignDimension] = {DesignDimension.COMPLEXITY, DesignDimension.CONSTRUCTION}
        for pattern, found in cls._PATH_RULES:
            if pattern.search(text):
                dimensions.update(found)

        keyword_rules = {
            DesignDimension.DOMAIN: r"\b(domain|bounded context|aggregate|invariant|ubiquitous language)\b",
            DesignDimension.DATA: r"\b(database|schema|cache|queue|event|stream|replicat|consistency|durab|partition|sql|n\+1|query|round trip)\b",
            DesignDimension.RESILIENCE: r"\b(timeout|retry|backoff|circuit|bulkhead|load shedding|failure|outage|health|performance|latency|throughput|allocation|hot path)\b",
            DesignDimension.REFACTORING: r"\b(refactor|rename|extract|cleanup|smell|restructure)\b",
            DesignDimension.LEGACY: r"\b(legacy|untested|characterization|seam|brownfield)\b",
            DesignDimension.COMPATIBILITY: r"\b(api|contract|migration|upgrade|backward|compatib|rollout|version|usage pattern|caller)\b",
            DesignDimension.ARCHITECTURE: r"\b(architect|boundary|dependency|adapter|framework|service|module|layer)\b",
            DesignDimension.SECURITY: r"\b(security|privacy|authorization|authentication|least privilege|threat model|secret|prompt injection|permission|sandbox)\b",
            DesignDimension.PERFORMANCE: r"\b(performance|benchmark|profiling|latency|throughput|resource budget|tail latency|big-o|load shedding)\b",
            DesignDimension.CONCURRENCY: r"\b(concurrency|parallel|race conditions?|deadlock|cancellation|shared state|thread safe|structured concurrency)\b",
            DesignDimension.DISTRIBUTED: r"\b(distributed|cap theorem|pacelc|partition|quorum|consensus|replication|eventual consistency|split brain|saga|outbox)\b",
            DesignDimension.CORRECTNESS: r"\b(invariant|transaction|isolation|data integrity|migration|reconciliation|serializability|source of truth)\b",
            DesignDimension.OPERATIONS: r"\b(rollback|recovery|observability|runbook|rto|rpo|slo|error budget|backup|restore|rollout)\b",
            DesignDimension.AI_AGENT: r"\b(ai agent|autonomous|agent|tool execution|prompt injection|model evaluation|human approval|agent memory)\b",
        }
        for dimension, pattern in keyword_rules.items():
            if re.search(pattern, text, re.I):
                dimensions.add(dimension)

        if risk.lower() in cls._HIGH_RISK:
            dimensions.update({DesignDimension.ARCHITECTURE, DesignDimension.RESILIENCE, DesignDimension.COMPATIBILITY})
        return tuple(sorted(dimensions, key=lambda d: d.value))

    @classmethod
    def review(
        cls,
        *,
        intent: str,
        changed_paths: Iterable[str] = (),
        design: Mapping[str, str] | None = None,
        risk: str = "medium",
        code_revision: str = "",
        blocking_dimensions: Iterable[DesignDimension] = (
            DesignDimension.ARCHITECTURE, DesignDimension.RESILIENCE,
            DesignDimension.SECURITY, DesignDimension.DATA, DesignDimension.OPERATIONS,
            DesignDimension.COMPATIBILITY, DesignDimension.LEGACY,
            DesignDimension.CONCURRENCY, DesignDimension.DISTRIBUTED,
            DesignDimension.CORRECTNESS, DesignDimension.AI_AGENT,
        ),
    ) -> DesignReviewReceipt:
        if not isinstance(risk, str) or risk.strip().lower() not in {"low", "medium", "high", "critical"}:
            raise ValueError("risk must be one of: low, medium, high, critical")
        normalized_risk = risk.strip().lower()
        if not isinstance(code_revision, str):
            raise ValueError("code_revision must be text")
        normalized_revision = code_revision.strip()
        raw_paths = tuple(changed_paths)
        if any(not isinstance(path, str) or not path.strip() for path in raw_paths):
            raise ValueError("changed paths must be non-empty strings")
        # Canonical path identity makes the receipt stable across input ordering
        # and binds the exact scope reviewed to its digest.
        paths = tuple(sorted(set(path.strip().replace("\\", "/") for path in raw_paths)))
        dimensions = cls.infer_dimensions(intent, paths, normalized_risk)
        declared = {str(k): str(v).strip() for k, v in (design or {}).items() if str(v).strip()}
        blocking_set = set(blocking_dimensions)
        findings: list[DesignFinding] = []
        high_risk = normalized_risk in cls._HIGH_RISK

        def is_not_applicable(dimension: DesignDimension, *keys: str) -> bool:
            names = keys or cls._DIMENSION_KEYS[dimension]
            return any(
                key in declared and declared[key].lower().startswith("not_applicable")
                for key in names
            )

        for dimension in dimensions:
            keys = cls._DIMENSION_KEYS[dimension]
            matching = next((key for key in keys if key in declared), None)
            if matching is None:
                severity = FindingSeverity.BLOCKING if dimension in blocking_set and high_risk else FindingSeverity.WARNING
                findings.append(DesignFinding(dimension, severity, f"Declare {dimension.value} evidence or an explicit not_applicable reason", None))
                continue
            value = declared[matching]
            if value.lower().startswith("not_applicable"):
                if ":" not in value and " - " not in value:
                    findings.append(DesignFinding(dimension, FindingSeverity.WARNING, "not_applicable must include a reason", matching))
            else:
                findings.append(DesignFinding(dimension, FindingSeverity.INFO, f"design contract declared for {dimension.value}", matching))

        if DesignDimension.REFACTORING in dimensions and not any(k in declared for k in ("behavior_delta", "safety_net")):
            findings.append(DesignFinding(DesignDimension.REFACTORING, FindingSeverity.WARNING, "Refactoring work should state behavior delta and safety net"))
        if DesignDimension.DATA in dimensions and not is_not_applicable(DesignDimension.DATA) and not any(k in declared for k in ("source_of_truth", "consistency", "idempotency", "db_access", "query_pattern")):
            severity = FindingSeverity.BLOCKING if high_risk else FindingSeverity.WARNING
            findings.append(DesignFinding(DesignDimension.DATA, severity, "Data changes should state source of truth and minimal DB/query access semantics"))
        if DesignDimension.RESILIENCE in dimensions and not is_not_applicable(DesignDimension.RESILIENCE) and not any(k in declared for k in ("timeout", "retry", "recovery", "performance", "resilience")):
            severity = FindingSeverity.BLOCKING if high_risk else FindingSeverity.WARNING
            findings.append(DesignFinding(DesignDimension.RESILIENCE, severity, "External/asynchronous/performance-sensitive work should state timeout, retry, recovery, and performance behavior"))

        # These dimensions require a concrete contract, not a broad declaration
        # that merely repeats the dimension name. Alternatives keep the policy
        # adaptable to different architectures while ensuring key failure modes
        # are considered explicitly.
        required_contracts = {
            DesignDimension.ARCHITECTURE: (
                (("responsibility", "module_owner", "boundary"), "State the responsible module/owner and its boundary, or an explicit not_applicable reason"),
                (("dependency_direction", "allowed_dependencies", "coupling"), "State permitted dependency direction and coupling constraints, or an explicit not_applicable reason"),
            ),
            DesignDimension.CONCURRENCY: (
                (("shared_state_owner", "single_writer", "lock_scope", "atomicity"), "State shared-state ownership and the lock/atomicity boundary, or an explicit not_applicable reason"),
                (("race_test", "interleaving_test", "concurrency_test"), "State race/interleaving regression coverage, or an explicit not_applicable reason"),
                (("cancellation", "synchronization", "deadlock", "parallelism"), "State cancellation/synchronization and race/deadlock verification, or an explicit not_applicable reason"),
            ),
            DesignDimension.DATA: (
                (("source_of_truth", "consistency", "transaction"), "State the authoritative data source and consistency/transaction boundary, or an explicit not_applicable reason"),
                (("schema_evolution", "migration", "backfill", "reconciliation"), "State schema/format evolution, backfill, reconciliation and rollback handling, or an explicit not_applicable reason"),
            ),
            DesignDimension.COMPATIBILITY: (
                (("usage_compatibility", "contract", "api_compatibility", "caller_compatibility"), "State compatibility for existing callers, APIs, workflow ordering and usage, or an explicit not_applicable reason"),
                (("migration", "rollout", "compatibility_plan", "rollback"), "State migration/rollout/rollback compatibility and sequencing, or an explicit not_applicable reason"),
            ),
            DesignDimension.LEGACY: (
                (("characterization", "regression"), "Record characterization/regression protection for existing behavior, or an explicit not_applicable reason"),
                (("seam", "dependency_break", "migration", "compatibility_plan"), "State the seam/dependency break and safe migration strategy, or an explicit not_applicable reason"),
            ),
            DesignDimension.RESILIENCE: (
                (("timeout_budget", "deadline_budget", "timeout", "deadline"), "State an operation deadline and timeout budget, or an explicit not_applicable reason"),
                (("retry_budget", "bounded_retries", "backoff", "jitter"), "State a bounded retry budget and backoff/jitter policy, or an explicit not_applicable reason"),
                (("recovery", "circuit_breaker", "bulkhead", "bounded_queue", "readiness", "liveness"), "State failure containment and recovery/readiness behavior, or an explicit not_applicable reason"),
            ),
            DesignDimension.SECURITY: (
                (("threat_model", "security"), "State the trust boundary and security threat/mitigation, or an explicit not_applicable reason"),
                (("authorization", "least_privilege", "tool_permissions"), "State authorization and least-privilege/tool authority boundaries, or an explicit not_applicable reason"),
            ),
            DesignDimension.PERFORMANCE: (
                (("performance_budget", "benchmark", "profiling", "latency", "resource_budget", "performance"), "State a measurable performance/resource expectation or explain why measurement is not applicable"),
            ),
            DesignDimension.DISTRIBUTED: (
                (("consistency", "cap_tradeoff", "pacelc"), "State consistency and CAP/PACELC trade-offs where partitions or replicas matter, or an explicit not_applicable reason"),
                (("idempotency", "ordering", "quorum", "split_brain"), "State duplicate delivery, ordering, quorum, or split-brain controls as applicable, or an explicit not_applicable reason"),
            ),
            DesignDimension.CORRECTNESS: (
                (("invariant", "correctness", "transaction", "source_of_truth"), "State critical invariants, transaction/source-of-truth semantics, or an explicit not_applicable reason"),
            ),
            DesignDimension.OPERATIONS: (
                (("rollback", "recovery", "rollout", "operations"), "State rollback/recovery and rollout behavior, or an explicit not_applicable reason"),
            ),
            DesignDimension.AI_AGENT: (
                (("agent_safety", "tool_permissions", "human_approval"), "State agent authority, tool permission and approval boundaries, or an explicit not_applicable reason"),
                (("evaluation", "provenance", "autonomy_budget"), "State evaluation/evidence provenance and bounded-autonomy criteria, or an explicit not_applicable reason"),
            ),
        }
        for dimension, groups in required_contracts.items():
            if dimension not in dimensions:
                continue
            for alternatives, message in groups:
                if any(key in declared and declared[key].lower().startswith("not_applicable") for key in alternatives):
                    reasoned = any(
                        key in declared and (":" in declared[key] or " - " in declared[key])
                        for key in alternatives
                    )
                    if not reasoned:
                        findings.append(DesignFinding(dimension, FindingSeverity.WARNING, "not_applicable must include a reason", alternatives[0]))
                    continue
                if not any(key in declared and not declared[key].lower().startswith("not_applicable") for key in alternatives):
                    severity = FindingSeverity.BLOCKING if high_risk and dimension in blocking_set else FindingSeverity.WARNING
                    findings.append(DesignFinding(dimension, severity, message, None))

        if cls._is_code_change(paths):
            for key, dimension, message in cls._UNIVERSAL_QUALITY:
                if key in declared and declared[key].lower().startswith("not_applicable"):
                    if ":" not in declared[key] and " - " not in declared[key]:
                        findings.append(DesignFinding(dimension, FindingSeverity.WARNING, "not_applicable must include a reason", key))
                    continue
                if key in declared:
                    findings.append(DesignFinding(dimension, FindingSeverity.INFO, f"change-quality contract declared for {key}", key))
                    continue
                severity = FindingSeverity.BLOCKING if high_risk else FindingSeverity.WARNING
                findings.append(DesignFinding(dimension, severity, message, None))

            if DesignDimension.DATA in dimensions:
                for key, message in (
                    ("query_pattern", "State how compatible reads are reused/batched and how N+1 or duplicate round trips are avoided"),
                ):
                    if key not in declared and not is_not_applicable(DesignDimension.DATA, "data", "db_access", "query_pattern", "n_plus_one"):
                        severity = FindingSeverity.BLOCKING if high_risk else FindingSeverity.WARNING
                        findings.append(DesignFinding(DesignDimension.DATA, severity, message, None))

        status = "blocked" if any(f.severity == FindingSeverity.BLOCKING for f in findings) else ("review_required" if any(f.severity == FindingSeverity.WARNING for f in findings) else "ready")
        payload = {
            "intent_digest": cls.intent_digest(intent),
            "dimensions": [d.value for d in dimensions],
            "declared": dict(sorted(declared.items())),
            "changed_paths": list(paths),
            "risk": normalized_risk,
            "code_revision": normalized_revision,
            "findings": [f.__dict__ | {"dimension": f.dimension.value, "severity": f.severity.value} for f in findings],
            "status": status,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return DesignReviewReceipt(
            cls.intent_digest(intent), tuple(d.value for d in dimensions), declared,
            tuple(findings), status, digest, paths, normalized_risk, normalized_revision,
        )

    @classmethod
    def as_principle_evidence_record(
        cls,
        receipt: DesignReviewReceipt,
        *,
        design: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Map the guard receipt to the canonical 12-family task evidence contract.

        This record is an applicability/evidence-routing receipt, not proof that
        the implementation is correct. Families not selected by the deterministic
        classifier remain operator-controlled rather than being silently marked N/A.
        """
        if not isinstance(receipt, DesignReviewReceipt):
            raise TypeError("receipt must be a DesignReviewReceipt")
        declared = {str(key): str(value).strip() for key, value in (design or {}).items() if str(value).strip()}
        family_dimensions = {
            "fundamental_design": (DesignDimension.COMPLEXITY, DesignDimension.ARCHITECTURE, DesignDimension.DOMAIN),
            "security_privacy": (DesignDimension.SECURITY,),
            "performance_cost": (DesignDimension.PERFORMANCE,),
            "concurrency": (DesignDimension.CONCURRENCY,),
            "distributed_systems": (DesignDimension.DISTRIBUTED,),
            "reliability_recovery": (DesignDimension.RESILIENCE,),
            "data_correctness": (DesignDimension.DATA, DesignDimension.CORRECTNESS),
            "api_integration": (DesignDimension.COMPATIBILITY,),
            "testing_verification": (DesignDimension.CONSTRUCTION, DesignDimension.CORRECTNESS),
            "delivery_operations": (DesignDimension.OPERATIONS, DesignDimension.COMPATIBILITY),
            "observability_governance": (DesignDimension.OPERATIONS, DesignDimension.RESILIENCE),
            "ai_autonomous_systems": (DesignDimension.AI_AGENT,),
        }
        selected = set(receipt.dimensions)
        families: dict[str, dict[str, object]] = {}
        for family, dimensions in family_dimensions.items():
            selected_dimensions = [dimension for dimension in dimensions if dimension.value in selected]
            na_reasons: list[str] = []
            enforced_dimensions: list[str] = []
            for dimension in dimensions:
                keys = cls._DIMENSION_KEYS[dimension]
                match = next((key for key in keys if key in declared), None)
                value = declared.get(match, "") if match else ""
                if value.lower().startswith("not_applicable") and (":" in value or " - " in value):
                    na_reasons.append(value.split(":", 1)[1].strip() if ":" in value else value.split(" - ", 1)[1].strip())
                elif dimension in selected_dimensions:
                    enforced_dimensions.append(dimension.value)
            if selected_dimensions and not enforced_dimensions and na_reasons:
                families[family] = {
                    "status": "not_applicable",
                    "reason": "; ".join(sorted(set(na_reasons))),
                    "guard_dimensions": [dimension.value for dimension in selected_dimensions],
                }
            elif selected_dimensions:
                families[family] = {
                    "status": "evidence_gated",
                    "reason": "EngineeringDesignGuard selected these dimensions; declarations are contracts, not behavioral proof.",
                    "guard_dimensions": [dimension.value for dimension in selected_dimensions],
                }
            elif na_reasons:
                families[family] = {
                    "status": "not_applicable",
                    "reason": "; ".join(sorted(set(na_reasons))),
                    "guard_dimensions": [],
                }
            else:
                families[family] = {
                    "status": "operator_controlled",
                    "reason": "The deterministic classifier did not select this family; the final reviewer/operator must assess applicability.",
                    "guard_dimensions": [],
                }
        payload: dict[str, object] = {
            "schema_version": "auren-engineering-principle-evidence-v1",
            "intent_digest": receipt.intent_digest,
            "code_revision": receipt.code_revision,
            "changed_paths": list(receipt.changed_paths),
            "risk": receipt.risk,
            "guard_status": receipt.status,
            "guard_receipt_digest": receipt.receipt_digest,
            "guard_receipt": cls.as_evidence(receipt),
            "families": families,
        }
        payload["digest"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return payload

    @staticmethod
    def validate_principle_verification_record(
        record: Mapping[str, object] | None,
        *,
        intent_digest: str,
        code_revision: str,
    ) -> dict[str, object]:
        """Validate final independent verification for all 12 principle families.

        This is an acceptance-boundary validator, not a generator. Every family
        must be explicitly verified or justified not-applicable with evidence;
        declarations and missing fields never count as proof.
        """
        blockers: list[str] = []
        if not isinstance(record, Mapping):
            return {
                "valid": False,
                "blockers": ["final principle verification receipt is missing"],
                "receipt_digest": "",
            }
        required_fields = {
            "schema_version", "intent_digest", "code_revision", "reviewer_id",
            "reviewer_version", "families", "digest",
        }
        if set(record) != required_fields:
            blockers.append("principle verification receipt fields are invalid")
        if record.get("schema_version") != "auren-engineering-principle-verification-v1":
            blockers.append("unsupported principle verification schema")
        if not isinstance(intent_digest, str) or not intent_digest.strip() or record.get("intent_digest") != intent_digest:
            blockers.append("principle verification intent digest is missing or stale")
        if not isinstance(code_revision, str) or not code_revision.strip() or record.get("code_revision") != code_revision:
            blockers.append("principle verification is not bound to the exact final code revision")
        for field in ("reviewer_id", "reviewer_version"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"unknown", "unconfigured", "unversioned"}:
                blockers.append(f"principle verification {field} is missing or untrusted")
        families = record.get("families")
        if not isinstance(families, Mapping) or set(families) != set(PRINCIPLE_FAMILY_IDS):
            blockers.append("principle verification must cover exactly all 12 canonical families")
        else:
            for family_id in PRINCIPLE_FAMILY_IDS:
                family = families.get(family_id)
                if not isinstance(family, Mapping) or set(family) != {"status", "reason", "evidence_refs"}:
                    blockers.append(f"{family_id}: verification entry is malformed")
                    continue
                status = family.get("status")
                reason = family.get("reason")
                refs = family.get("evidence_refs")
                if not isinstance(status, str) or status not in {"verified", "not_applicable", "blocked"}:
                    blockers.append(f"{family_id}: invalid verification status")
                elif status == "blocked":
                    blockers.append(f"{family_id}: principle verification is blocked")
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
                    blockers.append(f"{family_id}: a bounded reviewer reason is required")
                if (
                    not isinstance(refs, list) or not refs or len(refs) > 64
                    or any(not isinstance(ref, str) or not ref.strip() or len(ref) > 1024 for ref in refs)
                ):
                    blockers.append(f"{family_id}: verification requires bounded evidence references")
        supplied_digest = record.get("digest")
        try:
            payload = {key: value for key, value in record.items() if key != "digest"}
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if len(encoded) > 64_000:
                blockers.append("principle verification receipt exceeds the 64 KB limit")
            expected_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if not isinstance(supplied_digest, str) or supplied_digest != expected_digest:
                blockers.append("principle verification receipt digest mismatch")
        except (TypeError, ValueError):
            blockers.append("principle verification receipt is not canonical JSON")
            supplied_digest = ""
        return {
            "valid": not blockers,
            "blockers": sorted(set(blockers)),
            "receipt_digest": supplied_digest if isinstance(supplied_digest, str) else "",
        }

    @classmethod
    def recommend_for_repository(
        cls,
        repository_root: Path | str,
        intent: str,
        *,
        changed_paths: Iterable[str] = (),
        risk: str = "medium",
    ) -> RepositoryDesignRecommendation:
        """Suggest a repo-native approach from local manifests and existing code only.

        This is advisory evidence, not authorization or proof. Unknown stack/version
        facts remain explicit; the method never adds dependencies or assumes a framework.
        """
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        if not isinstance(risk, str) or risk.strip().lower() not in {"low", "medium", "high", "critical"}:
            raise ValueError("risk must be one of: low, medium, high, critical")
        root = Path(repository_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("repository_root must be an existing directory")
        task = intent.casefold()
        evidence: list[str] = []
        languages: list[str] = []
        frameworks: list[str] = []
        versions: list[str] = []
        manifest_text: dict[str, str] = {}

        def read_manifest(relative: str) -> str:
            path = root / relative
            if not path.is_file():
                return ""
            try:
                value = path.read_text(encoding="utf-8", errors="replace")[:100_000]
            except OSError:
                return ""
            evidence.append(relative)
            manifest_text[relative] = value
            return value

        pyproject = read_manifest("pyproject.toml")
        if pyproject or (root / "setup.py").is_file() or (root / "requirements.txt").is_file():
            languages.append("Python")
            match = re.search(r"requires-python\s*=\s*['\"]([^'\"]+)['\"]", pyproject, re.I)
            if match:
                versions.append(f"Python {match.group(1)} (declared constraint)")
            for framework in ("fastapi", "django", "flask", "pydantic", "pytest"):
                match = re.search(rf"(?i)['\"]?{framework}(?:\[[^\]]+\])?['\"]?\s*(?:==|>=|<=|~=|=)\s*['\"]?([0-9][^,'\"\s]*)", pyproject)
                if match:
                    frameworks.append(framework)
                    versions.append(f"{framework} {match.group(1)} (declared)")
            if "unittest" in task or any("unittest" in p.name for p in root.glob("tests/test_*.py")):
                frameworks.append("unittest (observed test convention)")

        package = read_manifest("package.json")
        if package:
            languages.append("JavaScript/TypeScript")
            try:
                parsed = json.loads(package)
                deps = {**parsed.get("dependencies", {}), **parsed.get("devDependencies", {})}
                for framework in ("react", "next", "vue", "angular", "express", "vitest", "jest"):
                    if framework in deps:
                        frameworks.append(framework)
                        versions.append(f"{framework} {deps[framework]} (declared)")
            except (TypeError, ValueError):
                pass

        cargo = read_manifest("Cargo.toml")
        if cargo:
            languages.append("Rust")
            match = re.search(r'(?m)^\s*edition\s*=\s*["\']([^"\']+)["\']', cargo)
            if match:
                versions.append(f"Rust edition {match.group(1)} (declared)")
            for framework in ("tokio", "axum", "actix-web", "serde"):
                match = re.search(rf"(?m)^\s*{re.escape(framework)}\s*=\s*['\"]?([^,\s#]+)", cargo)
                if match:
                    frameworks.append(framework)
                    versions.append(f"{framework} {match.group(1)} (declared)")

        go_mod = read_manifest("go.mod")
        if go_mod:
            languages.append("Go")
            match = re.search(r"(?m)^go\s+([0-9.]+)", go_mod)
            if match:
                versions.append(f"Go {match.group(1)} (declared)")

        project_files = sorted(root.glob("*.csproj"))[:20]
        if project_files:
            languages.append("C#/.NET")
            for path in project_files:
                value = path.read_text(encoding="utf-8", errors="replace")[:100_000]
                relative = path.relative_to(root).as_posix()
                evidence.append(relative)
                match = re.search(r"<TargetFrameworks?>\s*([^<]+)", value, re.I)
                if match:
                    versions.append(f".NET target {match.group(1).strip()} (declared in {relative})")
                for framework in ("xunit", "nunit", "mstest"):
                    if re.search(rf"(?i)PackageReference[^>]+Include=['\"]{framework}", value):
                        frameworks.append(framework)
        pom = read_manifest("pom.xml")
        if pom:
            languages.append("Java")
            match = re.search(r"<maven.compiler.(?:release|source)>\s*([^<]+)", pom, re.I)
            if match:
                versions.append(f"Java {match.group(1).strip()} (declared)")

        # Search only a bounded set of source files and skip generated/vendor trees.
        ignored = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "__pycache__"}
        source_files: list[Path] = []
        for folder in ("portable", "src", "app", "lib", "tests", "scripts"):
            base = root / folder
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if any(part in ignored for part in path.parts):
                    continue
                if path.is_file() and path.suffix.lower() in {".py", ".cs", ".ts", ".tsx", ".js", ".rs", ".go", ".java"}:
                    source_files.append(path)
                    if len(source_files) >= 80:
                        break
            if len(source_files) >= 80:
                break
        relevant_terms = {word for word in re.findall(r"[a-z][a-z0-9_]{2,}", task) if word not in {"implement", "support", "using", "with", "from", "into", "that", "this"}}
        reuse: list[str] = []
        for path in source_files:
            relative = path.relative_to(root).as_posix()
            if any(term in relative.casefold() for term in relevant_terms):
                reuse.append(relative)
        # AUREN's canonical owners are considered only when they exist in this repo.
        canonical = (
            "portable/execution_controller.py", "portable/context_engine.py",
            "portable/memory_orchestrator.py", "portable/persistent_memory.py",
            "portable/context_graph.py", ".ai-harness/runtime/context_cache.py",
            "portable/task_planner.py", "portable/engineering_design_guard.py",
        )
        for relative in canonical:
            if (root / relative).is_file() and any(term in relative.casefold() for term in relevant_terms):
                reuse.append(relative)
        for path in changed_paths:
            candidate = str(path).strip().replace("\\", "/")
            if candidate and (root / candidate).is_file():
                evidence.append(candidate)
                if candidate not in reuse:
                    reuse.append(candidate)
        reuse = list(dict.fromkeys(reuse))[:12]

        if re.search(r"quota|rate.?limit|retry|resume|provider|timeout|backoff", task):
            algorithm = "Persist a bounded state transition; honor provider retry/reset signals, otherwise use capped exponential backoff with jitter and a circuit breaker."
            workflow = ("Classify transient, quota, authorization and permanent failures separately",
                        "Commit checkpoint/evidence before waiting; never spin or poll while idle",
                        "Wake from the local scheduler, revalidate state/lease/permissions, then resume idempotently")
            patterns = ("State machine", "Circuit breaker", "Adapter", "Idempotent command")
            alternatives = ("Immediate retry is unsafe for exhausted quota and can create a busy loop",
                            "Provider switching is allowed only by explicit policy and equivalent trust constraints")
            tradeoffs = ("Durable checkpoints improve recovery but add state/schema lifecycle cost",
                         "Backoff protects provider budgets but increases completion latency")
            verification = ("Simulate Retry-After and unknown reset", "Prove no provider calls during the wait",
                            "Crash/restart test for duplicate side effects", "Test auth/permanent errors do not retry forever")
        elif re.search(r"memory|context|cache|token|compaction|recall", task):
            algorithm = "Use scoped retrieval over canonical durable records plus bounded task-local working state; reuse content-addressed cache and fail closed when required context does not fit."
            workflow = ("Resolve canonical owner and scope before reading or writing",
                        "Rank by relevance, verification and explicit priority under a hard budget",
                        "Persist only approved durable knowledge with source/evidence provenance")
            patterns = ("Repository", "Content-addressed cache", "Bounded context window", "Lazy TTL")
            alternatives = ("A second memory database duplicates truth and complicates recovery",
                            "Unbounded transcript replay increases cost and context-rot risk")
            tradeoffs = ("Strict budgets preserve liveness but may require staged retrieval",
                         "Process-local caches are cheap but must be reconstructible after restart")
            verification = ("Test scope, TTL, eviction and cache hits", "Assert required content is never silently truncated",
                            "Restart/compaction soak with bounded state", "Measure zero provider calls during idle")
        elif re.search(r"migration|schema|database|persistence|transaction|backfill", task):
            algorithm = "Use an idempotent, transactional migration with explicit invariants and reconciliation; prefer expand-contract when compatibility spans releases."
            workflow = ("Inventory existing schema and callers", "Add backward-compatible schema/reader first",
                        "Backfill in bounded batches with checkpoints", "Verify invariants, then retire legacy paths behind a rollback gate")
            patterns = ("Repository", "Unit of Work/transaction boundary", "Expand-contract migration", "Idempotent command")
            alternatives = ("Big-bang destructive migration increases rollback risk",
                            "Duplicated repositories make consistency and ownership ambiguous")
            tradeoffs = ("Compatibility phases take longer but make rollback tractable",
                         "Batching bounds memory and lock duration but needs progress tracking")
            verification = ("Test old/new schema compatibility", "Inject interruption and rerun safely",
                            "Reconcile counts and invariants", "Exercise rollback on a copy of representative state")
        else:
            algorithm = "Choose the smallest algorithm that satisfies the stated invariants; reuse an existing owner before introducing a new abstraction."
            workflow = ("Inspect call sites, manifests, tests and the nearest maintained implementation",
                        "State invariants, failure behavior and acceptance criteria before changing code",
                        "Implement one reversible slice and add regression tests at the public boundary",
                        "Review exact diff and run repository-standard CI before promotion")
            patterns = ("Small pure function where state is unnecessary", "Strategy only when behavior truly varies",
                        "Adapter at external boundaries", "Composition over parallel service abstractions")
            alternatives = ("A new generic framework is not justified without demonstrated variation",
                            "Refactor unrelated modules only when needed to make the target change safe")
            tradeoffs = ("Minimal change reduces regression surface but must not bypass required functionality",
                         "Additional abstraction is warranted only when it hides real complexity or isolates volatility")
            verification = ("Use the repository's existing test framework and naming conventions",
                            "Add boundary, failure-path and regression coverage", "Verify changed behavior on the exact final revision")
        warnings = ()
        if not languages:
            warnings = ("No recognized root manifest found; framework-specific idioms are intentionally withheld.",)
        if not versions:
            warnings += ("Framework/runtime versions were not declared in inspected manifests; verify them before using version-specific APIs.",)
        if not reuse:
            warnings += ("No directly matching maintained implementation was found in the bounded scan; inspect broader call sites before adding dependencies or abstractions.",)
        rollback = ("Preserve the last verified revision/checkpoint", "Keep changes behind a reversible migration or feature gate when state/behavior changes",
                    "Do not promote until regression, review and exact-head CI evidence pass")
        return RepositoryDesignRecommendation(
            status="advisory" if languages else "needs_stack_evidence",
            languages=tuple(dict.fromkeys(languages)),
            frameworks=tuple(dict.fromkeys(frameworks)),
            versions=tuple(dict.fromkeys(versions)),
            algorithm=algorithm,
            workflow=workflow,
            patterns=patterns,
            rationale=("Recommendations are derived from the task shape and repository-local manifests/source paths.",
                       "Patterns are suggestions, not mandates; existing code, correctness and measured constraints take precedence."),
            alternatives=alternatives,
            tradeoffs=tradeoffs,
            reuse_candidates=tuple(reuse),
            verification=verification,
            rollback=rollback,
            evidence_paths=tuple(dict.fromkeys(evidence)),
            warnings=warnings,
        )

    @staticmethod
    def as_evidence(receipt: DesignReviewReceipt) -> dict[str, object]:
        """Return a provenance-friendly projection without mutable receipt state."""
        return {
            "kind": "engineering_design_review",
            "digest": receipt.receipt_digest,
            "intent_digest": receipt.intent_digest,
            "changed_paths": list(receipt.changed_paths),
            "risk": receipt.risk,
            "code_revision": receipt.code_revision,
            "dimensions": list(receipt.dimensions),
            "status": receipt.status,
            "findings": [
                {"dimension": f.dimension.value, "severity": f.severity.value, "message": f.message, "evidence_key": f.evidence_key}
                for f in receipt.findings
            ],
        }
