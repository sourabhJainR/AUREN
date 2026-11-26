import unittest
from portable.optimizer_rubric_campaign import PairedTrial,analyze_campaign
def run():return dict(task_digest="t",source_digest="s",context_digest="c",tool_digest="x",provider_version="v",model_tier="standard",model_id="m",rubric_digest="r",policy_digest="p",privacy_scope_digest="q",acceptance_digest="a",context_budget_tokens=1000,input_tokens=100,output_tokens=20,latency_ms=100,cost_usd=.01,policy_violations=0)
def receipt(s):return {"status":"passed","weighted_score":s,"task_digest":"t"}
class TestCampaign(unittest.TestCase):
 def test_savings(self):
  b=run();o=dict(b,input_tokens=70,latency_ms=80,cost_usd=.006)
  x=analyze_campaign([PairedTrial("1",b,o,receipt(90),receipt(91))]);self.assertEqual(30,x["metrics"]["input_tokens"]["mean"]);self.assertTrue(x["promotion_eligible"])
 def test_violation_blocks(self):
  b=run();o=dict(b,policy_violations=1);self.assertFalse(analyze_campaign([PairedTrial("1",b,o,receipt(90),receipt(100))])["promotion_eligible"])
 def test_identity_mismatch(self):
  b=run();o=dict(b,model_id="other")
  with self.assertRaises(ValueError):analyze_campaign([PairedTrial("1",b,o,receipt(90),receipt(90))])
 def test_unscored_receipt(self):
  b=run()
  with self.assertRaises(ValueError):analyze_campaign([PairedTrial("1",b,b,receipt(90),dict(receipt(90),status="unscored"))])
if __name__=="__main__":unittest.main()
