import tempfile
import unittest
from pathlib import Path
from portable.autonomous_engineering_loop import AutonomousEngineeringLoop
from portable.engineering_evolution import EngineeringEvolutionControlPlane
from portable.persistent_memory import PersistentMemory

class ClosedLoopTests(unittest.TestCase):
    def test_noop_is_terminal_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            memory=PersistentMemory(Path(d)/"m.sqlite", require_approval=False)
            cp=EngineeringEvolutionControlPlane(memory,"p")
            loop=AutonomousEngineeringLoop(cp)
            receipt=loop.run(episode_id="e1",task_family="coding",capability="testing",
                             execute=lambda p: None,verify=lambda r:(True,("v1",)))
            self.assertTrue(receipt.accepted)
            self.assertEqual(receipt.terminal_action,"complete")
            self.assertEqual(receipt.iterations,1)

    def test_risky_history_gates_before_execution(self):
        with tempfile.TemporaryDirectory() as d:
            memory=PersistentMemory(Path(d)/"m.sqlite", require_approval=False)
            cp=EngineeringEvolutionControlPlane(memory,"p")
            cp.backlog.upsert(finding_id="f",task_family="coding",capability="testing",
                hat="quality",severity="critical",title="bad",detail="bad",
                recommendation="fix",attempts_increment=4)
            called=[]
            receipt=AutonomousEngineeringLoop(cp).run(
                episode_id="e2",task_family="coding",capability="testing",
                execute=lambda p: called.append(1),verify=lambda r:(True,("v",)))
            self.assertFalse(receipt.accepted)
            self.assertEqual(receipt.terminal_action,"escalate")
            self.assertFalse(called)

if __name__=="__main__": unittest.main()
