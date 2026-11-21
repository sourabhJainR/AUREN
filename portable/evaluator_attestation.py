"""Evaluator receipt integrity primitives. Keys must remain outside executor control."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, hmac, json
from typing import Mapping
from .evaluation_rubric import RubricScoreReceipt

def _canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

@dataclass(frozen=True)
class EvaluatorAttestation:
    evaluator_id: str
    evaluator_version: str
    receipt_digest: str
    key_id: str
    signature: str
    schema_version: str = "auren-evaluator-attestation-v1"
    def payload(self):
        return {"schema_version":self.schema_version,"evaluator_id":self.evaluator_id,"evaluator_version":self.evaluator_version,"receipt_digest":self.receipt_digest,"key_id":self.key_id}
    def as_dict(self): return {**self.payload(),"signature":self.signature}

def sign_receipt(receipt:RubricScoreReceipt,*,key_id:str,signing_key:bytes)->EvaluatorAttestation:
    if not isinstance(key_id,str) or not key_id.strip() or not isinstance(signing_key,bytes) or len(signing_key)<32:
        raise ValueError("key_id and 32-byte key required")
    payload={"schema_version":"auren-evaluator-attestation-v1","evaluator_id":receipt.evaluator_id,"evaluator_version":receipt.evaluator_version,"receipt_digest":receipt.receipt_digest,"key_id":key_id}
    signature=hmac.new(signing_key,_canonical(payload),hashlib.sha256).hexdigest()
    return EvaluatorAttestation(receipt.evaluator_id,receipt.evaluator_version,receipt.receipt_digest,key_id,signature)

def verify_attestation(receipt,attestation,*,trusted_keys:Mapping[str,bytes],key_authorizations:Mapping[str,tuple[str,tuple[str,...]]])->bool:
    """Authorization maps each key ID to its evaluator ID and allowed versions."""
    if not isinstance(attestation,EvaluatorAttestation): return False
    if (attestation.receipt_digest!=receipt.receipt_digest or attestation.evaluator_id!=receipt.evaluator_id or attestation.evaluator_version!=receipt.evaluator_version): return False
    if attestation.key_id not in trusted_keys or attestation.key_id not in key_authorizations: return False
    evaluator_id, versions=key_authorizations[attestation.key_id]
    if evaluator_id!=attestation.evaluator_id or attestation.evaluator_version not in versions: return False
    key=trusted_keys[attestation.key_id]
    if not isinstance(key,bytes) or len(key)<32: return False
    expected=hmac.new(key,_canonical(attestation.payload()),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,attestation.signature)
