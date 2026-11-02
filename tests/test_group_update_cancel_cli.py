"""Cancelling a grouped update stops public metadata waits before installation."""
import importlib.util
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ott_group_update_cancel", ROOT / "cli/app_update.py")
ota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ota)


class GroupUpdateCancellationTest(unittest.TestCase):
    def test_cancelled_metadata_fetch_does_not_open_a_connection(self):
        cancelled = threading.Event()
        cancelled.set()
        with mock.patch.object(ota.urllib.request, "build_opener") as opener:
            with self.assertRaises(KeyboardInterrupt):
                ota.request_json(ota.API, RuntimeError, group_cancelled=cancelled)
        opener.assert_not_called()

    def test_update_cancellation_wakes_metadata_wait_and_never_prepares_or_installs(self):
        cancelled, entered, release, closed = (threading.Event() for _ in range(4))
        client = SimpleNamespace(timeout=45, group_cancelled=cancelled,
                                 call=mock.Mock(return_value={"installed_code": 1}))
        runtime = SimpleNamespace(Error=RuntimeError, PlayerRejected=ValueError, PlayerUnsupported=TypeError,
                                  app_update_metadata=lambda value, params: value)
        opts = ota.options(["update", "--variant", "api22", "--version", "v1.2.3"], RuntimeError)
        case = self
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                closed.set()

            def read1(self, size):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("test did not release metadata response")
                return b"{}"

        def open_response(request, timeout):
            requests.append(request)
            case.assertEqual(request.full_url, ota.API + "/releases/tags/v1.2.3")
            case.assertIsNone(request.get_header("Authorization"))
            case.assertGreater(timeout, 0)
            case.assertLessEqual(timeout, 15)
            return Response()

        outcome = queue.Queue(maxsize=1)

        def update():
            try:
                ota.update(client, "device-one", opts, runtime)
            except BaseException as error:
                outcome.put(error)
            else:
                outcome.put(None)

        with mock.patch.object(ota.urllib.request, "build_opener",
                               return_value=SimpleNamespace(open=open_response)):
            thread = threading.Thread(target=update, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(5), "metadata reader did not start")
                cancelled.set()
                self.assertIsInstance(outcome.get(timeout=5), KeyboardInterrupt)
                self.assertFalse(closed.is_set(), "caller waited for the blocked HTTP reader")
                client.call.assert_called_once_with("device-one", "app_update", {"operation": "status"})
                self.assertEqual(client.timeout, 45)
                self.assertEqual(len(requests), 1)
            finally:
                cancelled.set()
                release.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(closed.wait(5), "metadata response was not closed after release")

    def test_group_metadata_success_retains_the_valid_json_result(self):
        cancelled = threading.Event()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read1.side_effect = [b'{"version":1}', b""]
        with mock.patch.object(ota.urllib.request, "build_opener",
                               return_value=SimpleNamespace(open=mock.Mock(return_value=response))):
            result = ota.request_json(ota.API, RuntimeError, deadline=time.monotonic() + 5,
                                      group_cancelled=cancelled)
        self.assertEqual(result, {"version": 1})
        response.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
