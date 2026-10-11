"""Least-disclosure per-case contract for evaluated Arena policies."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

_FORBIDDEN = frozenset({"case_id", "holdout", "is_holdout", "split", "manifest", "manifest_digest", "corpus_digest", "campaign_digest", "oracle_answer", "answer_key", "ground_truth", "receipt_key", "signing_key", "run_nonce"})

def reject_evaluator_fields(value: object) -> None:
    found: set[str] = set()
    def walk(item: object, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
                if normalized in _FORBIDDEN:
                    found.add(f"{path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item): walk(child, f"{path}[{index}]")
    walk(value, "policy")
    if found: raise ValueError("policy payload contains evaluator-only fields: " + ", ".join(sorted(found)))

@dataclass(frozen=True, slots=True)
class PolicyCaseInvocation:
    task: str
    input_payload: object
    environment: Mapping[str, object]
    protocol_version: str = "AUREN-POLICY-CASE-V1"
    def __post_init__(self) -> None:
        if not isinstance(self.task, str) or not self.task.strip(): raise ValueError("policy task must be non-empty text")
        if self.protocol_version != "AUREN-POLICY-CASE-V1": raise ValueError("unsupported policy case protocol")
        if not isinstance(self.environment, Mapping): raise ValueError("policy environment must be an object")
        reject_evaluator_fields({"task": self.task, "input": self.input_payload, "environment": self.environment})
    def to_bytes(self) -> bytes:
        payload = {"protocol_version": self.protocol_version, "task": self.task, "input": self.input_payload, "environment": dict(self.environment)}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

@dataclass(frozen=True, slots=True)
class PolicyCaseOutput:
    output: object
    output_digest: str
    @classmethod
    def from_untrusted(cls, value: object) -> "PolicyCaseOutput":
        reject_evaluator_fields(value)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return cls(value, hashlib.sha256(canonical).hexdigest())

__all__ = ["PolicyCaseInvocation", "PolicyCaseOutput", "reject_evaluator_fields"]
