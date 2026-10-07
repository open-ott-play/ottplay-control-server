import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resolve_ott", ROOT / "cli" / "ott.py")
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
REQUEST = {"version": 1, "query": "cats", "playlist_url": "https://proxy.example/private-token/playlist.m3u"}
RESULT = {"version": 1, "kind": "playlist", "matches": [{"name": "Cats", "url": "https://proxy.example/private-token/live.m3u8"}]}


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ResolveCliTest(unittest.TestCase):
    def make_symlink(self, link, target):
        try:
            link.symlink_to(target)
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) in (1, 50, 1314):
                self.skipTest("Creating symbolic links requires Windows privileges and filesystem support")
            raise

    def invoke(self, raw=None, argv=None, result=RESULT, exception=None, tty=False):
        output, errors = Terminal() if tty else io.StringIO(), io.StringIO()
        module = Mock()
        module.resolve.return_value = result
        module.resolve.side_effect = exception
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(REQUEST) if raw is None else raw)))
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(errors))
            stack.enter_context(patch.object(ott.Path, "is_file", return_value=True))
            stack.enter_context(patch.object(ott.importlib.util, "spec_from_file_location", return_value=Mock()))
            stack.enter_context(patch.object(ott.importlib.util, "module_from_spec", return_value=module))
            config = stack.enter_context(patch.object(ott, "read_json", side_effect=AssertionError("No controller configuration")))
            client = stack.enter_context(patch.object(ott, "Client", side_effect=AssertionError("No controller client")))
            status = ott.main(["resolve"] + (["--request-stdin"] if argv is None else argv))
        config.assert_not_called()
        client.assert_not_called()
        return status, output.getvalue(), errors.getvalue(), module

    def test_private_search_is_dispatched_without_configuration_or_playback(self):
        status, output, errors, module = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), RESULT)
        self.assertEqual(errors, "")
        module.resolve.assert_called_once_with(REQUEST)

    def test_sensitive_result_is_never_written_to_a_terminal(self):
        status, output, errors, module = self.invoke(tty=True)
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("Capture or redirect", errors)
        self.assertNotIn("private-token", errors)
        module.resolve.assert_not_called()

    def test_malformed_oversized_and_wrong_version_requests_are_rejected(self):
        for raw in ("private-token", "[1]", '{"version":true}', '{"version":2}',
                    "[" * 2000 + "]" * 2000,
                    " " * (64 * 1024 + 1), json.dumps({"version": 1, "query": "я" * 40000}, ensure_ascii=False)):
            with self.subTest(raw=raw[:24]):
                status, output, errors, module = self.invoke(raw=raw)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(output)["error"]["code"], "invalid_request")
                self.assertNotIn("private-token", output + errors)
                module.resolve.assert_not_called()

    def test_arguments_are_not_echoed_in_usage_errors(self):
        status, output, errors, module = self.invoke(argv=["https://proxy.example/private-token"])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "invalid_request")
        self.assertNotIn("private-token", output + errors)
        module.resolve.assert_not_called()

    def test_module_failures_never_expose_provider_data(self):
        for error in (ValueError(REQUEST["playlist_url"]), OSError(REQUEST["playlist_url"]),
                      RuntimeError(REQUEST["playlist_url"])):
            with self.subTest(error=type(error).__name__):
                status, output, errors, _ = self.invoke(exception=error)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(output)["error"]["code"], "resolution_failed")
                self.assertNotIn("private-token", output + errors)

    def test_invalid_or_oversized_module_result_is_not_emitted(self):
        for result in ({"version": True, "kind": "playlist", "matches": []},
                       {"version": 1, "kind": "other", "matches": []},
                       {"version": 1, "kind": "none", "matches": None},
                       {"version": 1, "kind": "playlist", "matches": ["private-token" * (2 * 1024 * 1024)]}):
            with self.subTest(kind=result["kind"]):
                status, output, errors, _ = self.invoke(result=result)
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(output)["error"]["code"], "resolution_failed")
                self.assertNotIn("private-token", output + errors)

    def test_interrupted_search_has_a_safe_status(self):
        status, output, errors, _ = self.invoke(exception=KeyboardInterrupt())
        self.assertEqual(status, 130)
        self.assertEqual(json.loads(output)["error"]["code"], "interrupted")
        self.assertEqual(errors, "")

    def test_help_never_reads_configuration_or_runs_search(self):
        status, output, errors, module = self.invoke(argv=["--help"], tty=True)
        self.assertEqual(status, 0)
        self.assertIn("--request-stdin", output)
        self.assertEqual(errors, "")
        module.resolve.assert_not_called()

    def test_symlink_resolves_the_sibling_module_and_needs_no_controller_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installation = root / "revision"
            installation.mkdir()
            (installation / "ott.py").write_bytes((ROOT / "cli" / "ott.py").read_bytes())
            (installation / "programme_search.py").write_bytes((ROOT / "cli" / "programme_search.py").read_bytes())
            (installation / "playlist_search.py").write_text(
                "def resolve(request):\n"
                "    return {'version': 1, 'kind': 'none', 'matches': []}\n")
            link = root / "ott"
            self.make_symlink(link, installation / "ott.py")
            completed = subprocess.run([sys.executable, str(link), "resolve", "--request-stdin"],
                                       input=json.dumps(REQUEST).encode("utf-8"), capture_output=True, timeout=10,
                                       env={**os.environ, "OTT_CONFIG": str(root / "missing-config.json")})
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), {"version": 1, "kind": "none", "matches": []})
            self.assertEqual(completed.stderr, b"")
            (installation / "playlist_search.py").unlink()
            completed = subprocess.run([sys.executable, str(link), "resolve", "--request-stdin"],
                                       input=json.dumps(REQUEST).encode("utf-8"), capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(json.loads(completed.stdout)["error"]["code"], "resolver_unavailable")

    def test_installed_symlink_searches_a_fixture_playlist_without_controller_settings(self):
        requests = []

        class PlaylistServer(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = '#EXTM3U\n#EXTINF:-1 tvg-id="cats",Три кота\n/private-token/live.m3u8\n'.encode("utf-8")
                self.send_response(200 if self.path == "/playlist.m3u" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), PlaylistServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installation = root / "revision"
                installation.mkdir()
                for name in ("ott.py", "playlist_search.py", "programme_search.py"):
                    (installation / name).write_bytes((ROOT / "cli" / name).read_bytes())
                link = root / "ott"
                self.make_symlink(link, installation / "ott.py")
                request = {"version": 1, "query": "три кота", "cache_dir": str(root / "cache"),
                           "playlist_url": f"http://127.0.0.1:{server.server_port}/playlist.m3u"}
                completed = subprocess.run([sys.executable, str(link), "resolve", "--request-stdin"],
                                           input=json.dumps(request, ensure_ascii=False).encode("utf-8"), capture_output=True,
                                           timeout=10, cwd=root,
                                           env={**os.environ, "OTT_CONFIG": str(root / "missing-config.json"),
                                                "PYTHONIOENCODING": "cp1252"})
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["version"], 1)
                self.assertEqual(result["kind"], "playlist")
                self.assertEqual(len(result["matches"]), 1)
                self.assertEqual(result["matches"][0]["name"], "Три кота")
                self.assertEqual(result["matches"][0]["url"],
                                 f"http://127.0.0.1:{server.server_port}/private-token/live.m3u8")
                self.assertEqual(requests, ["/playlist.m3u"])
                self.assertNotIn(b"private-token", completed.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
