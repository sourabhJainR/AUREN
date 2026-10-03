"""Paired counterfactual outcome attribution for execution strategy learning."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Sequence

@dataclass(frozen=True, slots=True)
class CounterfactualObservation:
    experiment_id: str
    case_id: str
    candidate_id: str
    quality: float
    duration: float
    cost: float
    passed: bool
    verified: bool = True
    oracle_independent: bool = True
    contaminated: bool = False

@dataclass(frozen=True, slots=True)
class CounterfactualAttribution:
    experiment_id: str
    case_ids: tuple[str, ...]
    treatment_candidate: str
    control_candidate: str
    treatment_quality: float
    control_quality: float
    quality_lift: float
    treatment_pass_rate: float
    control_pass_rate: float
    trustworthy: bool
    evidence_digest: str

class CounterfactualOutcomeAttributor:
    """Compare paired strategy outcomes without mutating routing or capabilities."""

    def evaluate(
        self,
        experiment_id: str,
        treatment_candidate: str,
        control_candidate: str,
        observations: Sequence[CounterfactualObservation],
    ) -> CounterfactualAttribution:
        if not experiment_id.strip() or not treatment_candidate.strip() or not control_candidate.strip():
            raise ValueError("experiment and candidate identities are required")
        if treatment_candidate == control_candidate:
            raise ValueError("treatment and control must differ")
        trusted = tuple(
            o for o in observations
            if o.experiment_id == experiment_id
            and o.verified and o.oracle_independent and not o.contaminated
            and o.candidate_id in {treatment_candidate, control_candidate}
        )
        cases = sorted({o.case_id for o in trusted})
        paired=[]
        for case in cases:
            t=[o for o in trusted if o.case_id==case and o.candidate_id==treatment_candidate]
            c=[o for o in trusted if o.case_id==case and o.candidate_id==control_candidate]
            if len(t)==1 and len(c)==1:
                paired.append((t[0],c[0]))
        if not paired:
            raise ValueError("no independently verified paired counterfactual observations")
        tq=sum(x.quality for x,_ in paired)/len(paired)
        cq=sum(y.quality for _,y in paired)/len(paired)
        tp=sum(x.passed for x,_ in paired)/len(paired)
        cp=sum(y.passed for _,y in paired)/len(paired)
        payload={"experiment_id":experiment_id,"cases":[x.case_id for x,_ in paired],
                 "treatment_candidate":treatment_candidate,"control_candidate":control_candidate,
                 "treatment_quality":round(tq,4),"control_quality":round(cq,4),
                 "treatment_pass_rate":round(tp,4),"control_pass_rate":round(cp,4)}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return CounterfactualAttribution(
            experiment_id, tuple(x.case_id for x,_ in paired), treatment_candidate,
            control_candidate, round(tq,4), round(cq,4), round(tq-cq,4),
            round(tp,4), round(cp,4), True, digest
        )
