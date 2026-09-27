import unittest

from portable.decision_fabric import (
    ChoiceDecision,
    DecisionFabric,
    DecisionQuestion,
    DecisionRecipe,
    NoulDecision,
    ScoreDecision,
)


class DecisionRecipeTests(unittest.TestCase):
    def test_recipe_groups_typed_questions_over_one_shared_state(self) -> None:
        recipe = DecisionRecipe(
            name="route-work",
            behavior="Select the safest execution route; do not invent a route.",
            questions=(
                DecisionQuestion("route", "choice", options=("local", "cloud", "no_match")),
                DecisionQuestion("risk", "score", levels=("low", "medium", "high")),
                DecisionQuestion("safe", "noul", claim="The proposed route is safe for this state."),
            ),
            thresholds={"review": 0.8},
            no_match={"route": "no_match"},
            source="decision-recipe-test",
        )
        calls = []

        def evaluate(state, question):
            calls.append((dict(state), question.key))
            if question.kind == "choice":
                return ChoiceDecision("local", {"local": 0.8, "cloud": 0.1, "no_match": 0.1}, 0.9)
            if question.kind == "score":
                return ScoreDecision("low", {"low": 0.8, "medium": 0.15, "high": 0.05}, 0.9)
            return NoulDecision(0.9, 0.9)

        batch = DecisionFabric(evaluate).evaluate_recipe({"cpu": 4, "risk": 0.1}, recipe)
        self.assertEqual(tuple(key for _, key in calls), ("route", "risk", "safe"))
        self.assertEqual({tuple(sorted(state.items())) for state, _ in calls}, {(('cpu', 4), ('risk', 0.1))})
        self.assertEqual(recipe.no_match["route"], "no_match")
        self.assertEqual(len(recipe.digest), 16)

    def test_recipe_rejects_invalid_no_match_and_duplicate_questions(self) -> None:
        q = DecisionQuestion("route", "choice", options=("local", "cloud"))
        with self.assertRaises(ValueError):
            DecisionRecipe("x", "behavior", (q, q))
        with self.assertRaises(ValueError):
            DecisionRecipe("x", "behavior", (q,), no_match={"route": "no_match"})


if __name__ == "__main__":
    unittest.main()
