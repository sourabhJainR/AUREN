from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portable.vibe_coding_adapters import AdapterUnavailable
from portable.vibe_coding_host import MCPStdioToolAdapter


class MCPOversizedResponseRecoveryTests(unittest.TestCase):
    def test_read_only_call_reduces_batch_after_real_oversized_jsonrpc_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            server = root / "server.py"
            calls = root / "calls.txt"
            server.write_text(
                "import json, sys\n"
                "calls_path = " + repr(str(calls)) + "\n"
                "for line in sys.stdin:\n"
                "    try: msg = json.loads(line)\n"
                "    except Exception: continue\n"
                "    method = msg.get('method')\n"
                "    if method == 'initialize':\n"
                "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'oversize-test','version':'1'}}}), flush=True)\n"
                "    elif method == 'tools/call':\n"
                "        limit = msg.get('params', {}).get('arguments', {}).get('limit')\n"
                "        with open(calls_path, 'a', encoding='utf-8') as out: out.write(str(limit) + '\\n')\n"
                "        items = ['x' * 1800] if limit >= 3 else ['recovered']\n"
                "        response = {'jsonrpc':'2.0','id':msg['id'],'result':{'structuredContent':{'items':items,'limit':limit}}}\n"
                "        print(json.dumps(response), flush=True)\n",
                encoding="utf-8",
            )

            def reduce_batch(arguments):
                reduced = dict(arguments)
                reduced["limit"] = max(1, int(reduced["limit"]) - 1)
                return reduced

            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="read_records",
                argument_map={"limit": "$limit"},
                timeout_seconds=5,
                max_response_chars=512,
                requires_authority_binding=False,
                trusted_workspace_root=workspace,
                read_only_response_reducer=reduce_batch,
            )
            result = adapter({
                "stage": "research",
                "intent": "read a bounded page",
                "limit": 3,
                "state": {"workspace_root": str(workspace)},
            })

            self.assertEqual(result, {"items": ["recovered"], "limit": 2})
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["3", "2"])

    def test_consequential_tools_cannot_use_oversized_response_recovery(self):
        with self.assertRaisesRegex(ValueError, "only allowed for operator-declared read-only"):
            MCPStdioToolAdapter(
                argv=(sys.executable, "-c", "pass"),
                tool_name="publish",
                read_only_response_reducer=lambda arguments: arguments,
            )


if __name__ == "__main__":
    unittest.main()



class MCPBoundedChunkRecoveryContractTests(unittest.TestCase):
    @staticmethod
    def _write_paged_server(path: Path) -> None:
        source = r"""
import json, sys
for line in sys.stdin:
    try: message = json.loads(line)
    except Exception: continue
    if message.get("method") == "initialize":
        response = {"jsonrpc":"2.0","id":message["id"],"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"chunk-contract-test","version":"1"}}}
        print(json.dumps(response), flush=True)
    elif message.get("method") == "tools/call":
        query = message.get("params", {}).get("arguments", {}).get("query")
        if query == "all":
            result = {"structuredContent":{"items":["x" * 1800]}}
        elif query == "page-a":
            result = {"structuredContent":{"items":["a" * 700]}}
        elif query == "page-b":
            result = {"structuredContent":{"items":["b" * 700]}}
        else:
            result = {"structuredContent":{"items":[]}}
        print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":result}), flush=True)
"""
        path.write_text(source, encoding="utf-8")

    def _adapter(self, root: Path, *, aggregate_limit: int):
        server = root / "chunk_server.py"
        self._write_paged_server(server)
        return MCPStdioToolAdapter(
            argv=(sys.executable, str(server)),
            tool_name="search",
            argument_map={"query": "$intent"},
            timeout_seconds=5,
            max_response_chars=1200,
            max_aggregate_response_chars=aggregate_limit,
            requires_authority_binding=False,
            trusted_workspace_root=root,
            read_only_response_chunker=lambda _args: [
                {"query": "page-a"}, {"query": "page-b"},
            ],
            read_only_response_merger=lambda pages: {
                "items": [item for page in pages for item in page["items"]],
            },
        )

    def test_explicit_read_only_chunks_recover_without_accepting_partial_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self._adapter(root, aggregate_limit=3000)
            result = adapter({
                "stage": "research", "intent": "all",
                "state": {"workspace_root": str(root)},
            })
            self.assertEqual(len(result["items"]), 2)
            self.assertEqual([item[0] for item in result["items"]], ["a", "b"])

    def test_chunk_results_cannot_exceed_aggregate_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self._adapter(root, aggregate_limit=1200)
            with self.assertRaisesRegex(AdapterUnavailable, "aggregate response budget"):
                adapter({
                    "stage": "research", "intent": "all",
                    "state": {"workspace_root": str(root)},
                })

    def test_chunk_plan_rejects_duplicate_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "unused.py"
            server.write_text("", encoding="utf-8")
            adapter = MCPStdioToolAdapter(
                argv=(sys.executable, str(server)),
                tool_name="search",
                timeout_seconds=5,
                max_response_chars=1200,
                requires_authority_binding=False,
                trusted_workspace_root=root,
                read_only_response_chunker=lambda _args: [
                    {"query": "same"}, {"query": "same"},
                ],
                read_only_response_merger=lambda pages: {"pages": list(pages)},
            )
            # First call is intentionally oversized, then the duplicate plan is rejected.
            server.write_text(
                "import json,sys\n"
                "for line in sys.stdin:\n"
                " m=json.loads(line)\n"
                " if m.get('method')=='initialize': print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'x','version':'1'}}}),flush=True)\n"
                " elif m.get('method')=='tools/call': print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'structuredContent':{'items':['x'*1800]}}}),flush=True)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AdapterUnavailable, "valid read-only chunk plan"):
                adapter({
                    "stage": "research", "intent": "all",
                    "state": {"workspace_root": str(root)},
                })
