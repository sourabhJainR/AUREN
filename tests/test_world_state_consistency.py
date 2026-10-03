import tempfile
import unittest
from portable.persistent_memory import PersistentMemory
from portable.world_model import Observation, WorldModel
from portable.world_state_consistency import WorldStateConsistencyGuard

class WorldStateConsistencyTests(unittest.TestCase):
    def model(self):
        m=WorldModel(PersistentMemory(tempfile.mktemp()),"p")
        m.observe(Observation("o1","e","status","ready","sensor-a",.9,observed_at="2026-10-04T00:00:00+00:00"))
        return m

    def test_single_state_can_be_selected_but_remains_confidence_aware(self):
        a=WorldStateConsistencyGuard().assess(self.model(),"e","status",now=__import__("datetime").datetime.fromisoformat("2026-10-04T01:00:00+00:00"))
        self.assertIsNotNone(a.selected)
        self.assertFalse(a.uncertain)

    def test_conflicting_observations_are_not_silently_collapsed(self):
        m=self.model()
        m.observe(Observation("o2","e","status","blocked","sensor-b",.95,observed_at="2026-10-04T00:30:00+00:00"))
        a=WorldStateConsistencyGuard().assess(m,"e","status",now=__import__("datetime").datetime.fromisoformat("2026-10-04T01:00:00+00:00"))
        self.assertEqual(len(a.hypotheses),2)
        self.assertIsNone(a.selected)
        self.assertTrue(a.uncertain)

    def test_digest_is_deterministic(self):
        m=self.model()
        g=WorldStateConsistencyGuard()
        self.assertEqual(g.assess(m,"e","status").assessment_digest,g.assess(m,"e","status").assessment_digest)

if __name__=="__main__":
    unittest.main()
