"""Adversarial engineering benchmark definitions and fail-closed scoring."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
@dataclass(frozen=True)
class AdversarialCase:
    case_id:str; category:str; prompt:str
@dataclass(frozen=True)
class AdversarialResult:
    case_id:str; passed:bool; evidence_id:str
class AdversarialBenchmark:
    def __init__(self,cases): self.cases=tuple(cases)
    def run(self,evaluator:Callable[[AdversarialCase],AdversarialResult]):
        results=tuple(evaluator(c) for c in self.cases)
        if any(r.case_id!=c.case_id for r,c in zip(results,self.cases)): raise ValueError("benchmark result mismatch")
        return results
    @staticmethod
    def pass_rate(results): return sum(r.passed for r in results)/len(results) if results else 0.0
