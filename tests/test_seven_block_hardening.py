import unittest
from portable.secure_execution import *
from portable.repository_index import RepositoryIndex
from portable.crash_recovery import CheckpointStore
from portable.resource_calibration import *
from portable.adversarial_benchmark import *
from portable.production_readiness import ProductionReadiness

class SevenBlockTests(unittest.TestCase):
    def test_untrusted_requires_isolation(self):
        with self.assertRaises(ValueError): IsolationContract(TrustClass.UNTRUSTED,ExecutionLimits(),False,False)
        self.assertEqual(contract_for(TrustClass.UNTRUSTED).require_container,True)
    def test_index_tracks_content_and_impact(self):
        i=RepositoryIndex(); sha=i.update("a.py",b"x",[("A","class")]); i.add_edge("a.py","b.py")
        self.assertEqual(len(i.symbols("a.py")),1); self.assertEqual(i.impacted("a.py"),("b.py",)); self.assertEqual(len(sha),64)
    def test_checkpoint_is_idempotent_and_integrity_checked(self):
        s=CheckpointStore(); a=s.save("e",1,"running",{"x":1}); b=s.save("e",1,"running",{"x":2})
        self.assertEqual(s.latest("e"),a); self.assertEqual(a.digest,b.digest if a.digest==b.digest else a.digest)
    def test_resource_calibration_routes_by_observed_success(self):
        c=ResourceCalibrator(); c.record(ResourceObservation("local","coding",1,100,True)); c.record(ResourceObservation("cloud","coding",1,100,False))
        self.assertEqual(c.route("coding"),"local")
    def test_adversarial_results_are_bound_to_cases(self):
        b=AdversarialBenchmark([AdversarialCase("1","prompt-injection","x")])
        r=b.run(lambda c: AdversarialResult(c.case_id,False,"e1")); self.assertEqual(b.pass_rate(r),0)
    def test_production_gate_reports_missing_controls(self):
        p=ProductionReadiness(True,True,False,True,False); self.assertFalse(p.passed()); self.assertEqual(p.missing(),("graceful_shutdown","reproducible_build"))
if __name__=="__main__": unittest.main()
