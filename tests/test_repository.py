import unittest
from pathlib import Path

from aura.engine import DecisionValidationError, apply_overrides, evaluate
from aura.repository import Repository
from tests.test_engine import sample_model


class RepositoryTests(unittest.TestCase):
    def setUp(self): self.repo = Repository(":memory:")

    def test_versioned_schema_and_local_password_sessions(self):
        self.assertEqual(self.repo.migration_version(), 4)
        self.assertFalse(self.repo.auth_configured())
        with self.assertRaises(DecisionValidationError): self.repo.set_password("short")
        self.repo.set_password("correct-horse-battery-staple")
        self.assertTrue(self.repo.auth_configured())
        self.assertTrue(self.repo.verify_password("correct-horse-battery-staple"))
        db = self.repo.connect()
        try:
            stored = db.execute("SELECT password_hash FROM auth_users WHERE id=1").fetchone()[0]
            self.assertNotEqual(stored, b"correct-horse-battery-staple")
            self.assertGreaterEqual(len(stored), 32)
        finally:
            if not self.repo._memory_conn: db.close()
        self.assertFalse(self.repo.verify_password("wrong-password"))
        token = "session-token-test"
        self.repo.create_session(token, "2999-01-01T00:00:00+00:00")
        self.assertTrue(self.repo.session_valid(token))
        self.repo.revoke_session(token)
        self.assertFalse(self.repo.session_valid(token))

    def test_workspace_exposes_keyboard_navigation_primitives(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "aura" / "static" / "index.html").read_text(encoding="utf-8")
        app = (root / "aura" / "static" / "app.js").read_text(encoding="utf-8")
        css = (root / "aura" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn('href="#content"', html)
        self.assertIn('role="tablist"', app)
        self.assertIn("ArrowLeft", app)
        self.assertIn("aria-selected", app)
        self.assertIn('for="evidenceQuery"', app)
        self.assertIn('aria-live="polite" aria-atomic="true" aria-label="Evidence suggestions"', app)
        self.assertIn('aria-label="Replay simulation from ${esc(label)}"', app)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', app)
        self.assertIn('for="robustnessOption"', app)
        self.assertIn(":focus-visible", css)
        self.assertIn(".sr-only", css)

    def test_sidebar_and_decision_table_avoid_page_level_horizontal_overflow(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "aura" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".decision-list{min-width:0;min-height:0", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn("-webkit-line-clamp:2", css)
        self.assertIn(".table-wrap{max-width:100%;overflow-x:auto", css)
        self.assertIn(".tabs{flex-wrap:wrap;overflow:visible}", css)

    def test_revision_and_evaluation_are_immutable(self):
        decision = self.repo.create_decision("Choose a role", "Which role fits?", sample_model())
        rev1 = decision["currentRevision"]["id"]
        eval1 = self.repo.save_evaluation(decision["id"], rev1, evaluate(decision["currentRevision"]["model"]))
        changed = sample_model(); changed["criteria"][0]["importance"] = 7
        updated = self.repo.save_revision(decision["id"], changed)
        self.assertEqual(updated["currentRevision"]["revisionNo"], 2)
        self.assertEqual(self.repo.get_evaluation(eval1["id"])["input_hash"], eval1["input_hash"])
        self.assertEqual(self.repo.get_revision(decision["id"], rev1)["model"]["criteria"][0]["importance"], 4)

    def test_scenario_ties_to_baseline_and_hashes_overrides(self):
        decision = self.repo.create_decision("Choose", "Which?", sample_model())
        rev = decision["currentRevision"]
        baseline = self.repo.save_evaluation(decision["id"], rev["id"], evaluate(rev["model"]))
        scenario = self.repo.create_scenario(decision["id"], baseline["id"], "More hours matter", {"weights":{"hours":8}})
        changed = apply_overrides(rev["model"], scenario["overrides"])
        run = self.repo.save_evaluation(decision["id"], rev["id"], evaluate(changed), scenario["id"], changed)
        self.assertEqual(self.repo.get_scenario(scenario["id"])["baseline_evaluation_id"], baseline["id"])
        self.assertNotEqual(run["input_hash"], baseline["input_hash"])
        self.assertEqual(self.repo.get_evaluation(baseline["id"])["result"], evaluate(rev["model"]))

    def test_delete_decision_removes_its_dependent_records(self):
        decision = self.repo.create_decision("Choose", "Which?", sample_model())
        rev = decision["currentRevision"]
        evaluation = self.repo.save_evaluation(decision["id"], rev["id"], evaluate(rev["model"]))
        self.repo.delete_decision(decision["id"])
        self.assertEqual(self.repo.list_decisions(), [])
        with self.assertRaises(KeyError): self.repo.get_evaluation(evaluation["id"])


if __name__ == "__main__": unittest.main()
