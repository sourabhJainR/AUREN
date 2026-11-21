import unittest
from portable.evaluation_rubric import RubricCriterion,RubricDefinition,evaluate_rubric
from portable.evaluator_attestation import sign_receipt,verify_attestation

def make_receipt(evaluator="qa",version="v1"):
    r=RubricDefinition("attestation-test","1",(RubricCriterion("correctness","Correct",100,("tests",),True),),hard_gate_ids=("gate",))
    return evaluate_rubric(r,subject_id="s",run_id="r",source_digest="a"*64,plan_digest="b"*64,artifact_digests=("c"*64,),evaluator_id=evaluator,evaluator_version=version,executor_id="builder",task_ids=("task",),scores={"correctness":100},evidence={"correctness":["tests://r"]},rationales={"correctness":"verified"},hard_gates={"gate":True})

class Tests(unittest.TestCase):
    def test_valid(self):
        r=make_receipt(); k=b"x"*32; a=sign_receipt(r,key_id="qa-key",signing_key=k)
        self.assertTrue(verify_attestation(r,a,trusted_keys={"qa-key":k},key_authorizations={"qa-key":("qa",("v1",))}))
    def test_unknown_key_version_tamper(self):
        r=make_receipt(); k=b"x"*32; a=sign_receipt(r,key_id="qa-key",signing_key=k)
        self.assertFalse(verify_attestation(r,a,trusted_keys={},key_authorizations={"qa-key":("qa",("v1",))}))
        self.assertFalse(verify_attestation(r,a,trusted_keys={"qa-key":k},key_authorizations={"qa-key":("qa",("v2",))}))
        bad=type(a)(a.evaluator_id,a.evaluator_version,a.receipt_digest,a.key_id,"0"*64)
        self.assertFalse(verify_attestation(r,bad,trusted_keys={"qa-key":k},key_authorizations={"qa-key":("qa",("v1",))}))
    def test_key_cannot_claim_other_evaluator(self):
        key=b"x"*32; receipt=make_receipt(evaluator="qa")
        forged=make_receipt(evaluator="other")
        att=sign_receipt(receipt,key_id="qa-key",signing_key=key)
        self.assertFalse(verify_attestation(forged,att,trusted_keys={"qa-key":key},key_authorizations={"qa-key":("qa",("v1",))}))
        # A key authorized for QA cannot attest a receipt claiming another evaluator.
        other=sign_receipt(forged,key_id="qa-key",signing_key=key)
        self.assertFalse(verify_attestation(forged,other,trusted_keys={"qa-key":key},key_authorizations={"qa-key":("qa",("v1",))}))
    def test_key_version_binding(self):
        r=make_receipt(); key=b"x"*32; a=sign_receipt(r,key_id="qa-key",signing_key=key)
        self.assertFalse(verify_attestation(r,a,trusted_keys={"qa-key":key},key_authorizations={"qa-key":("qa",("v2",))}))
