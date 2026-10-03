import unittest
from portable.long_horizon_campaign_manager import CampaignStep, LongHorizonCampaignManager

class LongHorizonCampaignTests(unittest.TestCase):
    def test_verified_progress_and_replanning(self):
        seen=[]
        m=LongHorizonCampaignManager(max_steps=5,budget_units=10,max_replans=2)
        def execute(s):
            seen.append(s.step_id)
            return s
        def verify(s,a):
            return (s.step_id != "bad", (f"e-{s.step_id}",))
        def replan(s,e):
            return [CampaignStep("repair","repair failed objective")] if s.step_id=="bad" else []
        r=m.run("c",[CampaignStep("ok","do ok"),CampaignStep("bad","do bad")],
                execute=execute,verify=verify,replan=replan)
        self.assertEqual(r.completed_step_ids,("ok","repair"))
        self.assertEqual(r.failed_step_ids,("bad",))
        self.assertFalse(r.accepted)  # failed original remains part of the evidence
        self.assertTrue(r.checkpoint.checkpoint_digest)

    def test_unverified_work_never_counts_as_completed(self):
        m=LongHorizonCampaignManager(max_steps=2,budget_units=2)
        r=m.run("c",[CampaignStep("s","do")],execute=lambda s:None,
                verify=lambda s,a:(False,("e",)))
        self.assertEqual(r.completed_step_ids,())
        self.assertEqual(r.failed_step_ids,("s",))
        self.assertTrue(r.interrupted)

    def test_budget_is_hard(self):
        m=LongHorizonCampaignManager(max_steps=2,budget_units=1)
        r=m.run("c",[CampaignStep("expensive","do",2)],execute=lambda s:None,verify=lambda s,a:(True,("e",)))
        self.assertTrue(r.interrupted)
        self.assertFalse(r.accepted)

if __name__=="__main__":
    unittest.main()
