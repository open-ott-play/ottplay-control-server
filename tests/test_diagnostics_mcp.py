import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
import diagnostics as diag
import diagnostics_mcp as mcp

TOKEN = "synthetic_mcp_credential_" + "y" * 32
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": mcp.PROTOCOL, "capabilities": {},
                         "clientInfo": {"name": "test-client", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class FakeClient:
    _token = TOKEN

    def __init__(self):
        self.calls = []

    def operation(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("session_id") == "fail":
            raise diag.DiagnosticError("timeout", unknown=True)
        return {"diagnostics_protocol": 2, "server_epoch": "epoch", "state": "start_pending"}

    runtimes = start = status = events = stop = revoke = repair = repair_status = operation


class MCPTests(unittest.TestCase):
    def adapter(self):
        adapter = mcp.Adapter(FakeClient())
        adapter.handle(INITIALIZE)
        adapter.handle(INITIALIZED)
        return adapter

    def test_subprocess_stdio_handshake_without_network(self):
        script = Path(__file__).resolve().parents[1] / "cli" / "diagnostics_mcp.py"
        messages = [INITIALIZE, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "eval", "arguments": {}}}]
        result = subprocess.run([sys.executable, str(script), "--server", "https://controller.invalid", "--token-env", "OTT_MCP_TEST"],
                                input="".join(json.dumps(row) + "\n" for row in messages), text=True,
                                capture_output=True, timeout=5, env={**os.environ, "OTT_MCP_TEST": TOKEN})
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]["result"]["protocolVersion"], mcp.PROTOCOL)
        tools = replies[1]["result"]["tools"]
        self.assertEqual(len(tools), 8)
        self.assertEqual([tool["name"] for tool in tools[:6]], [
            "diagnostics_runtimes", "diagnostics_start", "diagnostics_status",
            "diagnostics_events", "diagnostics_stop", "diagnostics_revoke",
        ])
        self.assertTrue(all("token" not in tool["inputSchema"]["properties"] for tool in tools))
        self.assertEqual(replies[2]["result"], {})
        self.assertEqual(replies[3]["error"]["code"], -32602)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_initialization_and_notifications(self):
        adapter = mcp.Adapter(FakeClient())
        query = {"jsonrpc": "2.0", "id": "q", "method": "tools/list"}
        self.assertIn("error", adapter.handle(query))
        response = adapter.handle({**INITIALIZE, "params": {**INITIALIZE["params"], "protocolVersion": "unknown"}})
        self.assertEqual(response["result"]["protocolVersion"], mcp.PROTOCOL)
        self.assertIn("error", adapter.handle(query))
        self.assertIsNone(adapter.handle(INITIALIZED))
        self.assertIn("result", adapter.handle(query))
        self.assertIn("error", adapter.handle(INITIALIZE))
        self.assertIsNone(adapter.handle({"jsonrpc": "2.0", "method": "notifications/unknown"}))

    def test_execution_errors_are_tool_errors(self):
        adapter = self.adapter()
        result = adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "diagnostics_status", "arguments": {"session_id": "fail"}}})["result"]
        self.assertTrue(result["isError"])
        self.assertTrue(result["structuredContent"]["error"]["unknown_outcome"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_unknown_arguments_do_not_call_transport(self):
        adapter = self.adapter()
        for arguments in ({}, {"session_id": "s", "url": "https://evil.example"}, {"session_id": "s", "token": TOKEN}):
            response = adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "diagnostics_status", "arguments": arguments}})
            self.assertTrue(response["result"]["isError"])
            self.assertNotIn(TOKEN, json.dumps(response))
        self.assertEqual(adapter.client.calls, [])

    def test_tool_schema_annotations(self):
        for tool in mcp.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertEqual(tool["annotations"]["readOnlyHint"], tool["name"] in mcp.READ_ONLY)
            self.assertFalse(tool["annotations"]["openWorldHint"])
        start = next(tool for tool in mcp.TOOLS if tool["name"] == "diagnostics_start")
        self.assertIn("idempotency_key", start["inputSchema"]["required"])
        self.assertIn("server_epoch", start["inputSchema"]["required"])
        repair = next(tool for tool in mcp.TOOLS if tool["name"] == "diagnostics_repair")
        self.assertEqual(repair["annotations"], {
            "readOnlyHint": False, "destructiveHint": True,
            "idempotentHint": False, "openWorldHint": False,
        })
        self.assertEqual(repair["inputSchema"]["properties"]["action"]["enum"], ["restart_stream", "reload_player"])
        self.assertEqual(set(repair["inputSchema"]["required"]), {
            "device_id", "runtime_id", "consent_epoch", "action", "deadline_ms", "idempotency_key", "server_epoch",
        })
        status = next(tool for tool in mcp.TOOLS if tool["name"] == "diagnostics_repair_status")
        self.assertTrue(status["annotations"]["readOnlyHint"])
        self.assertFalse(status["annotations"]["destructiveHint"])
        self.assertEqual(status["inputSchema"]["required"], ["repair_id"])

    def test_repair_tool_arguments_and_execution_errors(self):
        client = diag.DiagnosticsClient("https://controller.invalid", TOKEN)
        adapter = mcp.Adapter(client)
        adapter.handle(INITIALIZE)
        adapter.handle(INITIALIZED)
        arguments = {"device_id": "device", "runtime_id": "runtime", "consent_epoch": "consent",
                     "action": "reload_player", "deadline_ms": 10000,
                     "idempotency_key": "repair-key", "server_epoch": "epoch"}

        def call(name, args):
            return adapter.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": name, "arguments": args}})["result"]

        receipt = {"diagnostics_protocol": 2, "server_epoch": "epoch", "repair_id": "repair-one",
                   "state": "pending", "idempotency_retention_ms": 900000}
        with mock.patch.object(client, "_request", return_value=receipt) as send:
            result = call("diagnostics_repair", arguments)
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"], receipt)
            self.assertEqual(send.call_args.args[:2], ("POST", "/repairs"))
            self.assertEqual(send.call_args.args[2], arguments)
            self.assertEqual(send.call_args.kwargs["epoch"], "epoch")
            send.reset_mock()
            cases = [{}, {**arguments, "action": "eval"}, {**arguments, "action": ["reload_player"]},
                     {**arguments, "deadline_ms": True}, {**arguments, "deadline_ms": 30001},
                     {**arguments, "runtime_id": ""}, {**arguments, "token": TOKEN},
                     {**arguments, "url": "https://elsewhere.invalid"}]
            for args in cases:
                with self.subTest(args=args):
                    result = call("diagnostics_repair", args)
                    self.assertTrue(result["isError"])
                    self.assertNotIn(TOKEN, json.dumps(result))
            send.assert_not_called()
            call("diagnostics_repair_status", {"repair_id": "repair-one"})
            self.assertEqual(send.call_args.args, ("GET", "/repairs/repair-one"))
        with mock.patch.object(client, "_request", side_effect=diag.DiagnosticError("timeout", unknown=True)) as send:
            result = call("diagnostics_repair", arguments)
            self.assertTrue(result["isError"])
            self.assertTrue(result["structuredContent"]["error"]["unknown_outcome"])
            self.assertEqual(send.call_count, 1)

    def test_parse_errors_batch_rejection_and_size_limit(self):
        data = b'{"jsonrpc":"2.0","id":1,"id":2}\n[]\n' + b"x" * (mcp.MAX_MESSAGE + 1) + b"\n"
        output = io.StringIO()
        self.assertEqual(mcp.serve(FakeClient(), io.BytesIO(data), output), 1)
        codes = [json.loads(row)["error"]["code"] for row in output.getvalue().splitlines()]
        self.assertEqual(codes, [-32700, -32600, -32600])

    def test_utf8_only_frames_and_request_response_confusion(self):
        output = io.StringIO()
        raw = json.dumps(INITIALIZE).encode("utf-16") + b"\n"
        self.assertEqual(mcp.serve(FakeClient(), io.BytesIO(raw), output), 0)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], -32700)
        adapter = self.adapter()
        request = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "diagnostics_revoke", "arguments": {"runtime_id": "r", "server_epoch": "e"}}}
        for extra in ({"result": {}}, {"error": {}}):
            response = adapter.handle({**request, **extra})
            self.assertEqual(response["error"]["code"], -32600)
        self.assertEqual(adapter.client.calls, [])

    def test_missing_token_does_not_write_stdout(self):
        script = Path(__file__).resolve().parents[1] / "cli" / "diagnostics_mcp.py"
        env = dict(os.environ)
        env.pop("OTT_MCP_MISSING", None)
        result = subprocess.run([sys.executable, str(script), "--server", "https://controller.invalid", "--token-env", "OTT_MCP_MISSING"],
                                input="", capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_token")


if __name__ == "__main__":
    unittest.main()
