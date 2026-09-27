import tempfile,unittest
from pathlib import Path
from portable.continuous_engineering_decision_fabric import *
from portable.persistent_memory import PersistentMemory

class DecisionFabricTests(unittest.TestCase):
    def test_learns_metrics_and_routes_measured_path(self):
        with tempfile.TemporaryDirectory() as d:
            f=ContinuousEngineeringDecisionFabric(PersistentMemory(Path(d)/"m.sqlite",require_approval=False),"p")
            f.observe("coding","tests","slow","slow-tool",success=True,duration_seconds=100,cost=2,quality=.95)
            f.observe("coding","tests","fast","fast-tool",success=True,duration_seconds=10,cost=1,quality=.94)
            self.assertEqual(f.decide("coding","tests",[{"provider":"slow","tool_path":"slow-tool"},{"provider":"fast","tool_path":"fast-tool"}]).selected.provider,"fast")
    def test_guard_blocks_dirty_and_stale_work(self):
        c=RepositoryContext("repo","feature/x","origin/main","h","b",True,1,2,("x.py",))
        ok,reasons=RepositoryGuard.guard(c); self.assertFalse(ok)
        self.assertTrue(any("dirty" in x for x in reasons)); self.assertTrue(any("behind" in x for x in reasons))
    def test_canary_holds_then_promotes_or_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            f=ContinuousEngineeringDecisionFabric(PersistentMemory(Path(d)/"m.sqlite",require_approval=False),"p")
            one=[CounterfactualResult("c",.95,10,True,("e1",))]
            self.assertEqual(f.canary("cap","cand","base",one).status,"hold")
            rows=[CounterfactualResult(str(i),.95,10,True,(f"e{i}",)) for i in range(5)]
            self.assertEqual(f.canary("cap","cand","base",rows).status,"promote")
if __name__=="__main__": unittest.main()
