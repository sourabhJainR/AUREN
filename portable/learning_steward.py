"""Cross-agent learning extraction kept outside task execution."""
from __future__ import annotations
import json,re,sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
try:
    from runtime.task_memory import record
except ModuleNotFoundError:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/".ai-harness"))
    from runtime.task_memory import record
@dataclass(frozen=True)
class Learning:
    outcome:str; detail:str; approach:str=""; command:str=""
class LearningSteward:
    _line=re.compile(r"^-\s*(worked|failed|partial|regressed|not-applicable)\s*\|\s*(.*?)\s*\|\s*(.*)$",re.I)
    def __init__(self,root:Path,*,run_id:str,task:str)->None:self.root=Path(root);self.run_id=run_id;self.task=task
    def extract(self,output:str)->list[Learning]:
        active=False; found=[]
        for raw in str(output).splitlines():
            line=raw.strip()
            if line.lower().startswith(("## learnings","## lessons")):active=True;continue
            if active and line.startswith("## "):break
            if active:
                m=self._line.match(line)
                if m:
                    outcome,approach,detail=m.groups()
                    if detail.strip():found.append(Learning(outcome.lower(),detail.strip(),approach.strip()))
        return found
    def persist(self,output:str,*,evidence_ids:Iterable[str]=())->list[dict[str,object]]:
        evidence=list(evidence_ids); rows=[]
        for learning in self.extract(output):
            rows.append(record(self.root,task=self.task,category="approach",outcome=learning.outcome,detail=learning.detail,approach=learning.approach or None,run_id=self.run_id,evidence_ids=evidence,source_agent="learning-steward",promotion="candidate"))
        return rows
    @staticmethod
    def experience_history(root: Path, key: str, limit: int = 60) -> list[dict[str, Any]]:
        from runtime.task_memory import approach_history, relevant
        bounded_limit = max(1, int(limit))
        # Capability records must be attributable to the exact selected option.
        # Do not use relevance ranking here: unrelated failures can crowd the
        # target record out before filtering, and prefix matches can mix options.
        if ":capability:" in key or ":bundle:" in key or ":execution-strategy:" in key:
            return approach_history(Path(root), key, limit=bounded_limit, exact=True)
        rows = relevant(Path(root), key, limit=bounded_limit * 3)
        return [
            row for row in rows
            if str(row.get("approach", "")).startswith(key)
            or str(row.get("category", "")) == "decision-experience"
        ][:bounded_limit]

    def record_experience(
        self, *, key: str, outcome: str, evidence_quality: float,
        cost_score: float, duration_seconds: float, decision: str,
        evidence_ids: Iterable[str] = (),
    ) -> dict[str, object]:
        detail = json.dumps({
            "key": key,
            "decision": decision,
            "evidence_quality": round(max(0.0, min(1.0, float(evidence_quality))), 3),
            "cost_score": round(max(0.0, min(1.0, float(cost_score))), 3),
            "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
        }, sort_keys=True)
        return record(
            self.root, task=self.task, category="verification",
            outcome=("worked" if str(outcome) in {"passed","worked","success"} else ("regressed" if str(outcome) == "regressed" else "failed")), detail=detail, approach=key,
            run_id=self.run_id, evidence_ids=list(evidence_ids),
            source_agent="learning-steward", promotion="candidate",
        )

    @staticmethod
    def resource_history(root: Path, routing_key: str, limit: int = 50) -> list[dict[str, Any]]:
        from runtime.task_memory import relevant
        rows = relevant(Path(root), routing_key, limit=max(1, int(limit)) * 3)
        return [row for row in rows if row.get("approach") == routing_key][: max(1, int(limit))]

    def record_resource_outcome(self, *, routing_key: str, status: str, duration_seconds: float, memory_mb: int,
                               evidence_yield: float, failure_probability: float,
                               predicted_duration_seconds: float, predicted_memory_mb: int,
                               predicted_evidence_yield: float, evidence_ids: Iterable[str] = ()) -> dict[str, object]:
        outcome = "worked" if str(status) == "passed" else "failed"
        detail = json.dumps({"routing_key": routing_key, "duration_seconds": round(float(duration_seconds), 3),
            "memory_mb": int(memory_mb), "evidence_yield": round(max(0.0, min(1.0, float(evidence_yield))), 3),
            "failure_probability": round(max(0.0, min(1.0, float(failure_probability))), 3),
            "predicted_duration_seconds": round(float(predicted_duration_seconds), 3),
            "predicted_memory_mb": int(predicted_memory_mb),
            "predicted_evidence_yield": round(max(0.0, min(1.0, float(predicted_evidence_yield))), 3)}, sort_keys=True)
        return record(self.root, task=self.task, category="verification", outcome=outcome, detail=detail,
                      approach=routing_key, run_id=self.run_id, evidence_ids=list(evidence_ids),
                      source_agent="learning-steward", promotion="candidate")
    @staticmethod
    def prompt()->str:
        return """## Learning steward contract
You are not an execution agent. Review completed agent outcomes and capture only reusable lessons that can prevent future agents from repeating a mistake or reproduce a verified success.

Return a short `## LEARNINGS` section using exactly:
- worked | <approach> | <reusable lesson>
- failed | <approach> | <what failed and why>
- partial | <approach> | <what remains unsafe or incomplete>
- regressed | <approach> | <regression and evidence>

Keep useful detail within the hard memory guardrail. Record only evidence-backed observations. Do not turn guesses, generic advice, full logs, transcripts, or one-off task details into team memory. Execution agents stay focused on execution; you own learning and peripheral records.
"""
__all__=["Learning","LearningSteward"]
