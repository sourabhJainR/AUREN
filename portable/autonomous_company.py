"""Durable executive control loop for long-running AUREN work.

The supervisor is process-restart safe: every decision, blocker, alternate,
CI wait, review and trust update is persisted before the next transition.
Execution authority stays with the caller; this module owns orchestration,
evidence, recovery and learning state.
"""
from __future__ import annotations

import hashlib, json, sqlite3, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4

TERMINAL = {"completed", "failed", "cancelled"}
WAITING = {"waiting_ci", "waiting_external"}

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))

@dataclass(frozen=True)
class WorkUnit:
    id: str
    goal: str
    scope: str
    max_iterations: int = 1000000

@dataclass(frozen=True)
class Decision:
    id: str
    work_unit_id: str
    iteration: int
    action: str
    reason: str
    confidence: float
    alternatives: tuple[str, ...]
    selected_alternate: str | None
    evidence: tuple[str, ...]
    created_at: str

@dataclass(frozen=True)
class TrustScore:
    score: float
    confidence: float
    components: Mapping[str, float]
    evidence_count: int
    generated_at: str
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {"score": round(self.score, 4), "confidence": round(self.confidence, 4),
                "components": {k: round(v, 4) for k, v in self.components.items()},
                "evidence_count": self.evidence_count, "generated_at": self.generated_at,
                "digest": self.digest}

class AutonomousCompany:
    """One durable task owner: executive planning, execution, recovery, review and learning."""

    def __init__(self, root: str | Path, *, project: str = "AUREN") -> None:
        self.root = Path(root)
        self.db_path = self.root / ".auren" / "executive.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.project = project
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS work_units(
              id TEXT PRIMARY KEY, goal TEXT NOT NULL, scope TEXT NOT NULL,
              max_iterations INTEGER NOT NULL, state TEXT NOT NULL,
              iteration INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              work_key TEXT);
            CREATE TABLE IF NOT EXISTS decisions(
              id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, iteration INTEGER NOT NULL,
              action TEXT NOT NULL, reason TEXT NOT NULL, confidence REAL NOT NULL,
              alternatives TEXT NOT NULL, selected_alternate TEXT, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, iteration INTEGER NOT NULL,
              kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
              evidence TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes(
              id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, pattern TEXT NOT NULL,
              recipe TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS clarifications(
              id TEXT PRIMARY KEY, work_unit_id TEXT NOT NULL, question TEXT NOT NULL,
              dependency TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
        # Upgrade older executive ledgers in place. The key is optional for legacy
        # work units and unique for new idempotent start-or-resume requests.
        columns = {row[1] for row in self._db().execute("PRAGMA table_info(work_units)").fetchall()}
        if "work_key" not in columns:
            with self._db() as db:
                db.execute("ALTER TABLE work_units ADD COLUMN work_key TEXT")
        with self._db() as db:
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_work_units_work_key "
                "ON work_units(work_key) WHERE work_key IS NOT NULL AND work_key <> ''"
            )

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def start(
        self, goal: str, *, scope: str = "single-company-unit",
        max_iterations: int = 100000, work_key: str | None = None,
    ) -> WorkUnit:
        if not isinstance(goal, str) or not goal.strip(): raise ValueError("goal is required")
        if not isinstance(scope, str) or not scope.strip(): raise ValueError("scope is required")
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 100000:
            raise ValueError("max_iterations must be an integer between 1 and 100000")
        if work_key is not None and (not isinstance(work_key, str) or not work_key.strip()):
            raise ValueError("work_key must be a non-empty string when supplied")
        now = _utc()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if work_key is not None:
                existing = db.execute(
                    "SELECT id,goal,scope,max_iterations FROM work_units WHERE work_key=?",
                    (work_key.strip(),),
                ).fetchone()
                if existing:
                    db.commit()
                    if existing[1] != goal.strip() or existing[2] != scope.strip() or int(existing[3]) != max_iterations:
                        raise ValueError("work_key already belongs to a different goal, scope, or iteration budget")
                    return WorkUnit(existing[0], existing[1], existing[2], int(existing[3]))
            wid = uuid4().hex
            db.execute(
                "INSERT INTO work_units (id,goal,scope,max_iterations,state,iteration,created_at,updated_at,work_key) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (wid, goal.strip(), scope.strip(), max_iterations, "running", 0, now, now, work_key.strip() if work_key else None),
            )
            db.commit()
        return WorkUnit(wid, goal.strip(), scope.strip(), max_iterations)
    def resume(self, work_unit_id: str) -> WorkUnit:
        with self._db() as db:
            row = db.execute("SELECT id,goal,scope,max_iterations FROM work_units WHERE id=?", (work_unit_id,)).fetchone()
        if not row: raise KeyError(work_unit_id)
        return WorkUnit(*row)

    def _state(self, wid: str) -> tuple[str, int]:
        with self._db() as db:
            row = db.execute("SELECT state,iteration FROM work_units WHERE id=?", (wid,)).fetchone()
        if not row: raise KeyError(wid)
        return str(row[0]), int(row[1])

    def _transition(self, wid: str, state: str, iteration: int | None = None) -> None:
        with self._db() as db:
            if iteration is None:
                db.execute("UPDATE work_units SET state=?,updated_at=? WHERE id=?", (state,_utc(),wid))
            else:
                db.execute("UPDATE work_units SET state=?,iteration=?,updated_at=? WHERE id=?", (state,iteration,_utc(),wid))

    def hold_clarification(self, wid: str, question: str, *, dependency: str, evidence: Iterable[str] = ()) -> str:
        if not question.strip() or not dependency.strip():
            raise ValueError("question and dependency are required")
        cid = uuid4().hex
        refs = tuple(dict.fromkeys(str(x).strip() for x in evidence if str(x).strip()))
        with self._db() as db:
            db.execute("INSERT INTO clarifications VALUES(?,?,?,?,?,?,?)",
                       (cid, wid, question.strip(), dependency.strip(), "open", json.dumps(refs), _utc()))
        self.record(wid, "clarification", "held", question.strip(), refs + (f"dependency:{dependency}",))
        return cid

    def open_clarifications(self, wid: str) -> tuple[dict[str, object], ...]:
        with self._db() as db:
            rows = db.execute("SELECT id,question,dependency,state,evidence,created_at FROM clarifications WHERE work_unit_id=? AND state='open' ORDER BY created_at", (wid,)).fetchall()
        return tuple({"id":r[0],"question":r[1],"dependency":r[2],"state":r[3],"evidence":tuple(json.loads(r[4])),"created_at":r[5]} for r in rows)

    def resolve_clarification(self, clarification_id: str, resolution: str) -> None:
        if not isinstance(resolution, str) or not resolution.strip():
            raise ValueError("resolution is required")
        with self._db() as db:
            row = db.execute(
                "SELECT c.work_unit_id,w.state FROM clarifications c "
                "JOIN work_units w ON w.id=c.work_unit_id "
                "WHERE c.id=? AND c.state='open'",
                (clarification_id,),
            ).fetchone()
            if not row:
                raise KeyError(clarification_id)
            work_unit_id, work_state = row
            db.execute("UPDATE clarifications SET state='resolved' WHERE id=?", (clarification_id,))
            remaining = db.execute(
                "SELECT COUNT(*) FROM clarifications WHERE work_unit_id=? AND state='open'",
                (work_unit_id,),
            ).fetchone()[0]
            # Only unblock a waiting work unit. Never reopen a terminal unit just
            # because an old clarification was resolved later.
            if remaining == 0 and work_state == "waiting_external":
                db.execute(
                    "UPDATE work_units SET state='running',updated_at=? WHERE id=? AND state='waiting_external'",
                    (_utc(), work_unit_id),
                )
        self.record(work_unit_id, "clarification", "resolved", resolution, (f"clarification:{clarification_id}",))

    def record(self, wid: str, kind: str, status: str, detail: str, evidence: Iterable[str] = ()) -> str:
        state, iteration = self._state(wid)
        eid = uuid4().hex
        refs = tuple(dict.fromkeys(str(x).strip() for x in evidence if str(x).strip()))
        with self._db() as db:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                       (eid,wid,iteration,kind,status,detail,json.dumps(refs),_utc()))
        return eid

    def decide(self, wid: str, *, action: str, reason: str, confidence: float,
               alternatives: Sequence[str] = (), selected_alternate: str | None = None,
               evidence: Iterable[str] = ()) -> Decision:
        state, iteration = self._state(wid)
        if state in TERMINAL: raise RuntimeError("work unit is terminal")
        conf = _clamp(confidence)
        refs = tuple(dict.fromkeys(str(x).strip() for x in evidence if str(x).strip()))
        did = uuid4().hex
        now = _utc()
        with self._db() as db:
            db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (did,wid,iteration,action,reason,conf,json.dumps(tuple(alternatives)),
                        selected_alternate,json.dumps(refs),now))
        self.record(wid, "decision", "selected", reason, refs)
        return Decision(did,wid,iteration,action,reason,conf,tuple(alternatives),selected_alternate,refs,now)

    def blocker(self, wid: str, problem: str, *, alternatives: Sequence[str],
                confirm: Callable[[str], tuple[bool, str, Sequence[str]]],
                confidence: float = 0.5) -> Decision:
        """Evaluate alternatives without requiring user intervention.

        confirm(candidate) must return (acceptable, reason, evidence). The first
        acceptable candidate wins; every rejected candidate is retained as evidence.
        """
        if not alternatives: raise ValueError("at least one alternate is required")
        refs: list[str] = [self.record(wid,"blocker","detected",problem)]
        rejected: list[str] = []
        for candidate in alternatives:
            ok, reason, evidence = confirm(candidate)
            refs.extend(evidence)
            self.record(wid,"alternate","accepted" if ok else "rejected",f"{candidate}: {reason}",evidence)
            if ok:
                return self.decide(wid,action="alternate",reason=f"{problem}; {reason}",
                                    confidence=confidence,alternatives=alternatives,
                                    selected_alternate=candidate,evidence=refs)
            rejected.append(candidate)
        return self.decide(wid,action="escalate_or_wait",
                           reason=f"{problem}; no alternate passed confirmation",
                           confidence=min(confidence,0.25),alternatives=alternatives,evidence=refs)

    def wait_for_ci(
        self,
        wid: str,
        check: Callable[[], tuple[str, str, Sequence[str]]],
        poll_seconds: float = 30.0,
        timeout_seconds: float = 86400.0,
        *,
        stop: Callable[[], bool] | None = None,
        max_polls: int | None = None,
    ) -> str:
        """Wait for CI with validated time/poll budgets and cooperative cancellation.

        The callback must itself have a finite timeout: synchronous Python cannot
        safely interrupt a provider call that never returns. Between callback
        invocations, the deadline and stop signal are checked and sleeps are
        sliced to at most one second for responsive cancellation.
        """
        import math

        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("poll_seconds must be finite and positive")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if max_polls is not None and max_polls < 1:
            raise ValueError("max_polls must be positive when supplied")

        self._transition(wid, "waiting_ci")
        deadline = time.monotonic() + timeout_seconds
        polls = 0
        while True:
            if stop is not None and stop():
                self.record(wid, "ci", "cancelled", "CI wait cancelled by caller", ())
                return "cancelled"
            if time.monotonic() >= deadline:
                self.record(wid, "ci", "timeout", "CI wait deadline reached", ())
                return "timeout"
            if max_polls is not None and polls >= max_polls:
                self.record(wid, "ci", "poll_limit", "CI wait poll budget exhausted", ())
                return "poll_limit"

            status, detail, evidence = check()
            polls += 1
            if time.monotonic() >= deadline:
                self.record(wid, "ci", "timeout", "CI wait deadline reached during poll", ())
                return "timeout"
            if stop is not None and stop():
                self.record(wid, "ci", "cancelled", "CI wait cancelled by caller", ())
                return "cancelled"
            self.record(wid, "ci", status, detail, evidence)
            if status in {"passed", "failed"}:
                return status

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.record(wid, "ci", "timeout", "CI wait deadline reached", ())
                return "timeout"
            sleep_remaining = min(poll_seconds, remaining)
            while sleep_remaining > 0:
                if stop is not None and stop():
                    self.record(wid, "ci", "cancelled", "CI wait cancelled by caller", ())
                    return "cancelled"
                delay = min(1.0, sleep_remaining)
                time.sleep(delay)
                sleep_remaining -= delay

    def self_review(self, wid: str, checks: Sequence[Callable[[], tuple[bool,str,Sequence[str]]]]) -> tuple[bool, tuple[str,...]]:
        failures=[]; refs=[]
        for check in checks:
            ok, detail, evidence = check()
            refs.extend(evidence)
            self.record(wid,"self_review","passed" if ok else "failed",detail,evidence)
            if not ok: failures.append(detail)
        return not failures, tuple(refs)

    def trust(self, wid: str) -> TrustScore:
        with self._db() as db:
            rows=db.execute("SELECT kind,status,detail,evidence FROM events WHERE work_unit_id=?",(wid,)).fetchall()
        total=len(rows)
        def rate(kind, positive):
            subset=[r for r in rows if r[0]==kind]
            return sum(1 for r in subset if r[1] in positive)/len(subset) if subset else 0.0
        components={
          "execution": rate("execution",{"passed","completed"}),
          "verification": rate("verification",{"passed"}),
          "review": rate("self_review",{"passed"}),
          "ci": rate("ci",{"passed"}),
          "recovery": rate("alternate",{"accepted"}),
          "evidence": min(1.0,sum(len(json.loads(r[3])) for r in rows)/max(1,total*2)),
        }
        score=sum(components.values())/len(components)
        confidence=min(1.0, total/20.0) * (0.5 + 0.5*score)
        digest=_digest({"work_unit_id":wid,"components":components,"events":total})
        return TrustScore(score,confidence,components,total,_utc(),digest)

    def extract_recipe(self, wid: str, pattern: str, *, recipe: str, evidence: Iterable[str], confidence: float) -> str:
        rid=uuid4().hex; refs=tuple(dict.fromkeys(str(x) for x in evidence if str(x)))
        with self._db() as db:
            db.execute("INSERT INTO recipes VALUES(?,?,?,?,?,?,?)",(rid,wid,pattern,recipe,_clamp(confidence),json.dumps(refs),_utc()))
        return rid

    def finish(self, wid: str, *, success: bool, reason: str, evidence: Iterable[str] = ()) -> TrustScore:
        self.record(wid,"execution","completed" if success else "failed",reason,evidence)
        self._transition(wid,"completed" if success else "failed")
        return self.trust(wid)

    def _consecutive_failures(self, wid: str, kind: str, successful_statuses: set[str]) -> int:
        """Count consecutive non-success events for one gate from durable history."""
        with self._db() as db:
            rows = db.execute(
                "SELECT status FROM events WHERE work_unit_id=? AND kind=? ORDER BY rowid DESC LIMIT 100",
                (wid, kind),
            ).fetchall()
        count = 0
        for (status,) in rows:
            if status in successful_statuses:
                break
            count += 1
        return count

    def iterate(self, wid: str, *, execute: Callable[[int], tuple[str,str,Sequence[str]]],
                review: Callable[[int], tuple[bool,str,Sequence[str]]],
                ci: Callable[[int], tuple[str,str,Sequence[str]]] | None = None,
                recover: Callable[[str], tuple[bool,str,Sequence[str]]] | None = None,
                poll_seconds: float = 30.0, max_steps: int = 10,
                max_consecutive_failures: int = 3, ci_timeout_seconds: float = 300.0,
                max_ci_polls: int = 10) -> TrustScore:
        """Run a bounded slice of a durable work unit and yield safely for resume.

        One invocation executes at most max_steps iterations. The durable work unit
        remains resumable after a slice ends. Repeated gate failures stop the loop
        even if recovery keeps accepting an unproductive alternative; an unhandled
        review failure is terminal rather than an unbounded retry.
        """
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if isinstance(max_consecutive_failures, bool) or not isinstance(max_consecutive_failures, int) or max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be a positive integer")
        if poll_seconds <= 0 or ci_timeout_seconds <= 0:
            raise ValueError("poll_seconds and ci_timeout_seconds must be positive")
        if isinstance(max_ci_polls, bool) or not isinstance(max_ci_polls, int) or max_ci_polls < 1:
            raise ValueError("max_ci_polls must be a positive integer")
        unit = self.resume(wid)
        initial_state, initial_iteration = self._state(wid)
        if initial_state == "waiting_external":
            return self.trust(wid)
        if initial_state == "waiting_ci":
            if ci is None:
                return self.trust(wid)
            ci_status = self.wait_for_ci(
                wid, lambda: ci(initial_iteration), poll_seconds=poll_seconds,
                timeout_seconds=ci_timeout_seconds, max_polls=max_ci_polls,
            )
            if ci_status == "passed":
                with self._db() as db:
                    row = db.execute(
                        "SELECT evidence FROM events WHERE work_unit_id=? AND kind='verification' AND status='passed' ORDER BY rowid DESC LIMIT 1",
                        (wid,),
                    ).fetchone()
                evidence = tuple(json.loads(row[0])) if row else ()
                return self.finish(wid, success=True, reason="resumed CI passed after durable wait", evidence=evidence)
            if ci_status == "failed":
                if recover:
                    ok, why, refs = recover("CI " + ci_status)
                    self.record(wid, "recovery", "accepted" if ok else "rejected", why, refs)
                    if ok:
                        self._transition(wid, "running")
                    else:
                        return self.finish(wid, success=False, reason="CI did not pass after resume")
                else:
                    return self.finish(wid, success=False, reason="CI did not pass after resume")
            else:
                return self.trust(wid)
        steps = 0
        while steps < max_steps:
            state, iteration = self._state(wid)
            if state in TERMINAL:
                return self.trust(wid)
            if state in WAITING:
                return self.trust(wid)
            if iteration >= unit.max_iterations:
                return self.finish(wid, success=False, reason="iteration limit reached")
            iteration += 1
            steps += 1
            self._transition(wid, "running", iteration)
            status, detail, evidence = execute(iteration)
            self.record(wid, "execution", status, detail, evidence)
            if status == "blocked":
                self.hold_clarification(wid, detail, dependency="execution blocked", evidence=evidence)
                if recover:
                    ok, why, refs = recover(detail)
                    self.record(wid, "recovery", "accepted" if ok else "held", why, refs)
                    if ok:
                        continue
                self._transition(wid, "waiting_external", iteration)
                return self.trust(wid)
            if status not in {"passed", "completed"}:
                failures = self._consecutive_failures(wid, "execution", {"passed", "completed", "blocked"})
                if failures >= max_consecutive_failures:
                    return self.finish(wid, success=False, reason="repeated execution failure without progress", evidence=evidence)
                if recover:
                    ok, why, refs = recover(detail)
                    self.record(wid, "recovery", "accepted" if ok else "rejected", why, refs)
                    if ok:
                        continue
                return self.finish(wid, success=False, reason=detail, evidence=evidence)

            reviewed, review_detail, refs = review(iteration)
            if not reviewed:
                self.record(wid, "verification", "failed", review_detail, refs)
                failures = self._consecutive_failures(wid, "verification", {"passed"})
                if failures >= max_consecutive_failures:
                    return self.finish(wid, success=False, reason="repeated review failure without progress", evidence=refs)
                if recover:
                    ok, why, recovery_refs = recover(review_detail)
                    self.record(wid, "alternate", "accepted" if ok else "rejected", why, recovery_refs)
                    if ok:
                        continue
                return self.finish(wid, success=False, reason="review failed without a verified recovery path", evidence=refs)

            self.record(wid, "verification", "passed", review_detail, refs)
            if ci:
                ci_status = self.wait_for_ci(
                    wid, lambda: ci(iteration), poll_seconds=poll_seconds,
                    timeout_seconds=ci_timeout_seconds, max_polls=max_ci_polls,
                )
                if ci_status == "passed":
                    return self.finish(wid, success=True, reason="execution, review and CI passed", evidence=refs)
                if ci_status in {"failed", "timeout"}:
                    if recover:
                        ok, why, recovery_refs = recover("CI " + ci_status)
                        self.record(wid, "recovery", "accepted" if ok else "rejected", why, recovery_refs)
                        if ok:
                            continue
                    return self.finish(wid, success=False, reason="CI did not pass", evidence=refs)
                # Cancellation/poll exhaustion is a resumable wait, never success.
                return self.trust(wid)
            return self.finish(wid, success=True, reason="execution and review passed", evidence=refs)

        state, iteration = self._state(wid)
        if state not in TERMINAL and state not in WAITING:
            self.record(wid, "supervisor", "yielded", "bounded iteration slice exhausted; work unit remains resumable", (f"next_iteration:{iteration + 1}",))
        return self.trust(wid)

__all__=["AutonomousCompany","Decision","TrustScore","WorkUnit"]
