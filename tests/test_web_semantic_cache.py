"""Web-run cache eligibility tests without model or network transport."""
import unittest
from unittest.mock import Mock, patch

import web.app as web


class WebSemanticCacheEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.state = web.app.state
        self.runs = self.state.runs
        self.original_runs = self.state.runs
        self.state.runs = {}
        self.session_store = Mock()
        self.session_store.get_session.return_value = None
        self.session_store.create_session.return_value = {"id": "session-1"}
        self.session_store.get_session_persona.return_value = None
        self.store = Mock()
        self.prompts = Mock()
        self.prompts.get_active_version.return_value = None

        self.patches = [
            patch.object(self.state, "session_store", self.session_store),
            patch.object(self.state, "store", self.store),
            patch.object(self.state, "prompts", self.prompts),
            patch.object(self.state, "guardrails", None),
            patch.object(self.state, "workspace_writer", None),
            patch.object(self.state, "searcher", Mock()),
            patch("web.app.load_personas", return_value={}),
            patch.object(web.settings, "check_ollama_alive", return_value=True),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.addCleanup(self._restore_runs)

    def _restore_runs(self):
        self.state.runs = self.original_runs

    def _start(self, results, mode="ollama"):
        messages = [
            {"role": "system", "content": "You are Sensai."},
            {"role": "user", "content": "Question"},
        ]
        with patch("web.app.assemble_turn", return_value=(messages, "Question", results, "")):
            response = web.start_run(web.RunRequest(message="Question", mode=mode))
        return self.state.runs[response["run_id"]]

    def test_standalone_ollama_web_turn_is_cache_eligible(self):
        run = self._start([])
        self.assertTrue(run["cache_context_independent"])

    def test_retrieved_or_search_web_turn_is_not_cache_eligible(self):
        retrieved = [{"source_file": "facts.md", "content": "Context"}]
        self.assertFalse(self._start(retrieved)["cache_context_independent"])
        self.assertFalse(self._start([], mode="search")["cache_context_independent"])


if __name__ == "__main__":
    unittest.main()
