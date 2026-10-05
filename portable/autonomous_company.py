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
              iteration INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
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

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def start(self, goal: str, *, scope: str = "single-company-unit", max_iterations: int = 1000000) -> WorkUnit:
        if not goal.strip(): raise ValueError("goal is required")
        wid = uuid4().hex
        now = _utc()
        with self._db() as db:
            db.execute("INSERT INTO work_units VALUES(?,?,?,?,?,?,?,?)",
                       (wid, goal.strip(), scope.strip(), max_iterations, "running", 0, now, now))
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
        with self._db() as db:
            row=db.execute("SELECT work_unit_id FROM clarifications WHERE id=? AND state='open'", (clarification_id,)).fetchone()
            if not row:
                raise KeyError(clarification_id)
            db.execute("UPDATE clarifications SET state='resolved' WHERE id=?", (clarification_id,))
        self.record(row[0], "clarification", "resolved", resolution, (f"clarification:{clarification_id}",))

    def record(self, wid: str, kind: str, status: str, detail: str, evidence: Iterable[str] = ()) -> str:
        state, iteration = self._state(wid)
        eid = uuid4().hex
        refs = tuple(dict.fromkeys(str(x).strip() for x in evidence if str(x).strip()))
        with self._db() as db:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
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

    def wait_for_ci(self, wid: str, check: Callable[[], tuple[str, str, Sequence[str]]],
                    poll_seconds: float = 30.0, timeout_seconds: float = 86400.0) -> str:
        self._transition(wid,"waiting_ci")
        deadline = time.monotonic() + timeout_seconds
        while True:
            status, detail, evidence = check()
            self.record(wid,"ci",status,detail,evidence)
            if status in {"passed","failed"}:
                return status
            if time.monotonic() >= deadline:
                self.record(wid,"ci","timeout","CI wait deadline reached",())
                return "timeout"
            time.sleep(max(0.1,poll_seconds))

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

    def iterate(self, wid: str, *, execute: Callable[[int], tuple[str,str,Sequence[str]]],
                review: Callable[[int], tuple[bool,str,Sequence[str]]],
                ci: Callable[[int], tuple[str,str,Sequence[str]]] | None = None,
                recover: Callable[[str], tuple[bool,str,Sequence[str]]] | None = None,
                poll_seconds: float = 30.0) -> TrustScore:
        """Run until terminal state; restart-safe because every boundary is persisted."""
        unit=self.resume(wid)
        while True:
            state, iteration=self._state(wid)
            if state in TERMINAL: return self.trust(wid)
            if iteration >= unit.max_iterations:
                return self.finish(wid,success=False,reason="iteration limit reached")
            iteration += 1
            self._transition(wid,"running",iteration)
            status,detail,evidence=execute(iteration)
            self.record(wid,"execution",status,detail,evidence)
            if status == "blocked":
                self.record(wid, "clarification", "held", detail, evidence)
                if recover:
                    ok,why,refs=recover(detail)
                    self.record(wid, "recovery", "accepted" if ok else "held", why, refs)
                    if ok:
                        continue
                self._transition(wid, "waiting_external", iteration)
                return self.trust(wid)
            if status not in {"passed","completed"}:
                if recover:
                    ok,why,refs=recover(detail)
                    self.record(wid,"recovery","accepted" if ok else "rejected",why,refs)
                    if ok: continue
                return self.finish(wid,success=False,reason=detail,evidence=evidence)
            ok,detail,refs=review(iteration)
            if not ok:
                self.record(wid,"verification","failed",detail,refs)
                if recover:
                    ok2,why,refs2=recover(detail)
                    self.record(wid,"alternate","accepted" if ok2 else "rejected",why,refs2)
                    if ok2: continue
                continue
            self.record(wid,"verification","passed",detail,refs)
            if ci:
                ci_status=self.wait_for_ci(wid,lambda:ci(iteration),poll_seconds=poll_seconds)
                if ci_status=="passed":
                    return self.finish(wid,success=True,reason="execution, review and CI passed",evidence=refs)
                if ci_status in {"failed","timeout"}:
                    if recover:
                        ok2,why,refs2=recover("CI "+ci_status)
                        self.record(wid,"recovery","accepted" if ok2 else "rejected",why,refs2)
                        if ok2: continue
                    return self.finish(wid,success=False,reason="CI did not pass",evidence=refs)
            return self.finish(wid,success=True,reason="execution and review passed",evidence=refs)

__all__=["AutonomousCompany","Decision","TrustScore","WorkUnit"]
