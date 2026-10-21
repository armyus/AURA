import unittest

from aura.engine import DecisionValidationError, apply_overrides, evaluate, robustness, sensitivity, validate_model
from aura.ingestion import search_chunks


def sample_model():
    return {
        "options": [{"id": "internship", "name": "Internship"}, {"id": "research", "name": "Research"}],
        "criteria": [
            {"id": "mentor", "name": "Mentorship", "direction": "benefit", "importance": 4, "valueType": "ordinal"},
            {"id": "hours", "name": "Hours", "direction": "cost", "importance": 2, "valueType": "ordinal",
             "constraint": {"operator": "lte", "threshold": 4}},
        ],
        "assessments": {
            "internship": {"mentor": {"value": 5}, "hours": {"value": 4}},
            "research": {"mentor": {"value": 3}, "hours": {"value": 2}},
        },
    }


class DecisionEngineTests(unittest.TestCase):
    def test_normalizes_weights_and_explains_contributions(self):
        result = evaluate(sample_model())
        self.assertEqual(result["status"], "complete")
        self.assertAlmostEqual(sum(result["normalizedWeights"].values()), 1)
        self.assertAlmostEqual(sum(c["contribution"] for c in result["options"][0]["contributions"]), result["options"][0]["score"])
        self.assertEqual(result["recommendation"], "internship")

    def test_evaluation_carries_source_provenance(self):
        model = sample_model()
        model["assessments"]["internship"]["mentor"] = {"value": 5, "status": "verified", "source": {"chunkId": "chunk-1", "filename": "offer.txt", "pageNo": None}}
        result = evaluate(model)
        record = next(p for p in result["assessmentProvenance"] if p["criterionId"] == "mentor")
        self.assertEqual(record["status"], "verified")
        self.assertEqual(record["source"]["chunkId"], "chunk-1")

    def test_hard_constraint_cannot_be_offset_by_score(self):
        model = sample_model()
        model["criteria"][1]["constraint"] = {"operator": "lte", "threshold": 2}
        result = evaluate(model)
        internship = next(x for x in result["options"] if x["optionId"] == "internship")
        self.assertEqual(internship["constraintStatus"], "fail")
        self.assertIsNone(internship["rank"])
        self.assertEqual(result["recommendation"], "research")

    def test_missing_information_suppresses_recommendation(self):
        model = sample_model()
        del model["assessments"]["research"]["hours"]
        result = evaluate(model)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["options"][1]["missing"], ["Hours"])

    def test_close_scores_are_tied(self):
        model = sample_model()
        model["criteria"] = [{"id":"a","name":"A","direction":"benefit","importance":1,"valueType":"ordinal"},
                              {"id":"b","name":"B","direction":"benefit","importance":1,"valueType":"ordinal"}]
        model["assessments"] = {"internship":{"a":{"value":4},"b":{"value":4}},"research":{"a":{"value":4},"b":{"value":4}}}
        result = evaluate(model)
        self.assertEqual(result["status"], "tied")
        self.assertEqual({x["rank"] for x in result["options"]}, {1})
        self.assertIsNone(result["recommendation"])

    def test_numeric_cost_and_target_transforms(self):
        model = sample_model()
        model["criteria"] = [
            {"id":"cost","name":"Cost","direction":"cost","importance":1,"valueType":"numeric","scale":{"min":0,"max":100}},
            {"id":"hours","name":"Weekly hours","direction":"target","importance":1,"valueType":"numeric","scale":{"min":0,"max":60,"target":20,"tolerance":20}},
        ]
        model["assessments"] = {"internship":{"cost":{"value":20},"hours":{"value":20}},"research":{"cost":{"value":60},"hours":{"value":40}}}
        result = evaluate(model)
        self.assertEqual(result["recommendation"], "internship")
        self.assertEqual(result["options"][0]["displayScore"], 0.9)

    def test_scenario_overrides_do_not_mutate_baseline(self):
        model = sample_model()
        original = evaluate(model)
        scenario = apply_overrides(model, {"weights":{"hours":8},"assessments":{"research":{"mentor":5}}})
        changed = evaluate(scenario)
        self.assertEqual(evaluate(model), original)
        self.assertNotEqual(changed["options"][0]["score"], original["options"][0]["score"])

    def test_weight_sensitivity_is_deterministic(self):
        a = sensitivity(sample_model(), "mentor", 1, 5, 11)
        b = sensitivity(sample_model(), "mentor", 1, 5, 11)
        self.assertEqual(a, b)
        self.assertEqual(len(a["points"]), 11)
        self.assertEqual(a["tornado"]["leader"], "Internship")
        self.assertEqual(a["tornado"]["runnerUp"], "Research")
        self.assertTrue(a["tornado"]["drivers"])

    def test_robustness_is_reproducible_and_reports_uncertainty(self):
        model = sample_model()
        ranges = {"internship": {"mentor": [2, 5]}}
        first = robustness(model, ranges, {"hours": [1, 6]}, 250, 17)
        second = robustness(model, ranges, {"hours": [1, 6]}, 250, 17)
        self.assertEqual(first, second)
        self.assertEqual(first["iterations"], 250)
        self.assertTrue(0 <= first["noClearWinnerShare"] <= 1)
        self.assertTrue(all(0 <= row["winShare"] <= 1 for row in first["options"]))
        self.assertTrue(all(row["scoreP05"] <= row["scoreP95"] for row in first["options"] if row["scoreP05"] is not None))

    def test_robustness_rejects_out_of_domain_or_missing_ranges(self):
        with self.assertRaises(DecisionValidationError):
            robustness(sample_model(), {"internship": {"mentor": [0, 5]}}, iterations=100)
        model = sample_model()
        del model["assessments"]["research"]["hours"]
        with self.assertRaises(DecisionValidationError):
            robustness(model, {"research": {"hours": [2, 4]}}, iterations=100)

    def test_invalid_numeric_scale_is_rejected(self):
        model = sample_model(); model["criteria"][0]["valueType"] = "numeric"; model["criteria"][0]["scale"] = {"min":4,"max":4}
        with self.assertRaises(DecisionValidationError): validate_model(model)

    def test_retrieval_ranks_matching_source_passage(self):
        chunks = [{"id":"1","text":"The internship includes a weekly mentor meeting and code review."},
                  {"id":"2","text":"The research role focuses on lab work and publication."}]
        self.assertEqual(search_chunks(chunks, "mentor meeting")[0]["id"], "1")


if __name__ == "__main__": unittest.main()
