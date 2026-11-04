import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import httpx

from session import SessionStore
from web import app as web_module


class SessionClearTests(unittest.TestCase):
    def setUp(self):
        self.app = web_module.app
        self.old_store = self.app.state.session_store
        self.old_runs = self.app.state.runs
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(Path(self.tmp.name) / "s.db")
        self.app.state.session_store = self.store
        self.app.state.runs = {}
        self.addCleanup(self._restore)

    def _restore(self):
        self.app.state.session_store = self.old_store
        self.app.state.runs = self.old_runs

    def test_clear_removes_all_messages_and_resets_title(self):
        s = self.store.create_session("New session", session_id="a")
        self.store.add_message("a", "user", "hello world")
        self.store.add_message("a", "assistant", "hi")
        self.store.add_message("a", "assistant", "partial", status="failed")
        self.assertEqual(self.store.get_session("a")["title"], "hello world")

        result = web_module.clear_session("a")

        self.assertEqual(result["removed_messages"], 3)
        self.assertEqual(self.store.get_messages("a", ("completed", "failed", "cancelled")), [])
        self.assertEqual(self.store.get_session("a")["title"], "New session")

    def test_clear_cancels_in_flight_runs_of_that_session_only(self):
        self.store.create_session("New session", session_id="a")
        mine = {"session_id": "a", "stream": SimpleNamespace(finished=False)}
        other = {"session_id": "b", "stream": SimpleNamespace(finished=False)}
        self.app.state.runs = {"r1": mine, "r2": other}

        result = web_module.clear_session("a")

        self.assertEqual(result["cancelled_runs"], 1)
        self.assertTrue(mine["cancelled"] and mine["cleared"])
        self.assertNotIn("cancelled", other)

    def test_clear_unknown_session_is_a_noop(self):
        self.assertEqual(web_module.clear_session("ghost")["removed_messages"], 0)

    def test_clear_and_delete_cancel_loop_tasks_from_http_worker(self):
        async def check():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for method, path in (("POST", "/api/sessions/a/clear"),
                                     ("DELETE", "/api/sessions/a")):
                    with self.subTest(method=method):
                        if self.store.get_session("a") is None:
                            self.store.create_session(session_id="a")
                        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]
                        await asyncio.sleep(0)
                        self.app.state.runs = {"r1": {
                            "session_id": "a", "stream": SimpleNamespace(finished=False),
                            "search_task": tasks[0], "mcp_task": tasks[1],
                        }}
                        try:
                            response = await client.request(method, path)
                            self.assertEqual(response.status_code, 200, response.text)
                            for task in tasks:
                                with self.assertRaises(asyncio.CancelledError):
                                    await asyncio.wait_for(task, timeout=1)
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(check(), debug=True)

    def test_delete_session(self):
        self.store.create_session("x", session_id="a")
        self.store.add_message("a", "user", "q")
        self.assertEqual(web_module.delete_session("a")["status"], "deleted")
        self.assertIsNone(self.store.get_session("a"))
        with self.assertRaises(HTTPException):
            web_module.delete_session("a")

    def test_long_first_prompt_is_truncated_for_title(self):
        self.store.create_session("New session", session_id="a")
        self.store.add_message("a", "user", "word " * 40)
        title = self.store.get_session("a")["title"]
        self.assertLessEqual(len(title), 50)
        self.store.add_message("a", "user", "second prompt")
        self.assertEqual(self.store.get_session("a")["title"], title)


if __name__ == "__main__":
    unittest.main()
