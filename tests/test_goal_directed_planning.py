import unittest
from portable.goal_directed_planning import Goal, GoalDirectedPlanner

class GoalTests(unittest.TestCase):
    def test_dependency_order_and_priority(self):
        planner=GoalDirectedPlanner()
        goals=(Goal("root","root",.5,"complete"),
               Goal("a","a",.6,depends_on=("root",)),
               Goal("b","b",.9,depends_on=("root",)),
               Goal("c","c",1.0,depends_on=("b",)))
        self.assertEqual(planner.next_goal(goals).goal_id,"b")
        self.assertEqual(planner.blocked(goals)[0].goal_id,"c")

if __name__=="__main__":
    unittest.main()
