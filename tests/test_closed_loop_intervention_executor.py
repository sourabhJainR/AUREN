import unittest
from portable.counterfactual_outcome_attribution import CounterfactualObservation, CounterfactualOutcomeAttributor
from portable.longitudinal_transfer_evaluator import LongitudinalTransferEvaluator, TransferObservation
from portable.evidence_to_learning_intervention import EvidenceToLearningInterventionController
from portable.closed_loop_intervention_executor import (
    InterventionAuthorization, InterventionVerification, ClosedLoopInterventionExecutor,
)


def decision():
    rows=[]
    for i in range(3):
        rows += [
            CounterfactualObservation("e1",f"case-{i}","t",.9,1,1,True),
            CounterfactualObservation("e1",f"case-{i}","c",.7,1,1,True),
        ]
    a=CounterfactualOutcomeAttributor().evaluate("e1","t","c",rows)
    t=LongitudinalTransferEvaluator().evaluate("cap",[
        TransferObservation("x","a","d1","novel",.9,.9),
        TransferObservation("y","b","d2","novel",.9,.9),
        TransferObservation("z","a","d1","holdout",.8,.9),
    ])
    return EvidenceToLearningInterventionController().decide(
        capability="cap", attribution=a, transfer=t, prior_campaign_digest="camp"
    )


class ClosedLoopExecutionTests(unittest.TestCase):
    def test_approved_execution_requires_exact_digests_and_blocks_promotion(self):
        d=decision(); applied=[]; rolled=[]
        e=ClosedLoopInterventionExecutor(
            apply=lambda x: applied.append(x) or {"candidate": x.intervention_id},
            verify=lambda x: InterventionVerification(True, True, True, False, ("fresh-holdout-1",)),
            rollback=lambda x: rolled.append(x),
        )
        auth=InterventionAuthorization(
            d.decision_digest, d.intervention.intervention_digest, "approval-1", "external-reviewer"
        )
        r=e.run(d,auth,execution_id="run-1")
        self.assertTrue(r.applied); self.assertTrue(r.verified)
        self.assertFalse(r.rolled_back); self.assertTrue(r.promotion_blocked)
        self.assertEqual(r.evidence_ids, ("fresh-holdout-1",))

    def test_failed_verification_rolls_back(self):
        d=decision(); rolled=[]
        e=ClosedLoopInterventionExecutor(
            apply=lambda x: {"candidate": x.intervention_id},
            verify=lambda x: InterventionVerification(False, True, True, False, ("failed-holdout",)),
            rollback=lambda x: rolled.append(x),
        )
        auth=InterventionAuthorization(d.decision_digest,d.intervention.intervention_digest,"a","reviewer")
        r=e.run(d,auth,execution_id="run-2")
        self.assertTrue(r.applied); self.assertFalse(r.verified); self.assertTrue(r.rolled_back)
        self.assertTrue(r.safe_terminal); self.assertEqual(len(rolled),1)

    def test_mismatched_authorization_fails_closed(self):
        d=decision()
        e=ClosedLoopInterventionExecutor(apply=lambda x:x,verify=lambda x: InterventionVerification(True, True, True, False, ("e",)),rollback=lambda x:None)
        auth=InterventionAuthorization("wrong",d.intervention.intervention_digest,"a","reviewer")
        with self.assertRaises(ValueError):
            e.run(d,auth,execution_id="run-3")


if __name__=="__main__":
    unittest.main()
\n    def test_untrusted_retest_evidence_rolls_back(self):\n        d=decision(); rolled=[]\n        e=ClosedLoopInterventionExecutor(\n            apply=lambda x: {"candidate": x.intervention_id},\n            verify=lambda x: InterventionVerification(True, False, True, False, ("not-fresh",)),\n            rollback=lambda x: rolled.append(x),\n        )\n        auth=InterventionAuthorization(d.decision_digest,d.intervention.intervention_digest,"a","reviewer")\n        r=e.run(d,auth,execution_id="run-4")\n        self.assertFalse(r.verified); self.assertTrue(r.rolled_back); self.assertTrue(r.safe_terminal)\n