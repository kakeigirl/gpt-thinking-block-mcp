import contextlib
import io
import pathlib
import tempfile
import unittest

import server


class ProtocolTests(unittest.TestCase):
    def test_initialize(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["serverInfo"]["name"], "gpt-thinking-block-mcp")

    def test_tool_is_listed(self):
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = response["result"]["tools"][0]
        self.assertEqual(tool["name"], "render_thinking_block")
        self.assertIn("scoped to the current turn", tool["description"])
        self.assertIn("The user may see this block", tool["description"])
        self.assertIn("never include passwords, tokens, personal identifiers", tool["description"])
        self.assertIn("temporary work scoped to the current turn", tool["description"])
        self.assertIn("normal user-facing final answer", tool["description"])
        thinking_description = tool["inputSchema"]["properties"]["thinking"]["description"]
        self.assertIn("Use the user's main language", thinking_description)
        self.assertIn("style=relational", thinking_description)
        self.assertIn("do not quote them later", thinking_description)
        effort_description = tool["inputSchema"]["properties"]["effort"]["description"]
        self.assertIn("Approximate token band", effort_description)
        self.assertIn("medium is over 700 and up to 1000", effort_description)
        self.assertIn("high is over 1200 and up to 2000", effort_description)
        self.assertEqual(
            tool["inputSchema"]["properties"]["effort"]["enum"],
            ["low", "medium", "high"],
        )
        self.assertNotIn("soft generation targets", effort_description)
        skin = tool["inputSchema"]["properties"]["skin"]
        self.assertEqual(skin["enum"], ["botanical", "microglow"])
        self.assertIn("glass-like morning light", skin["description"])
        self.assertIn("skin", tool["inputSchema"]["required"])

    def test_original_chinese_prompt_edition_is_available(self):
        self.assertEqual(server.normalize_prompt_language("zh"), "zh-CN")
        self.assertEqual(server.normalize_prompt_language("zh_CN"), "zh-CN")
        thinking_description = server.THINKING_DESCRIPTIONS["zh-CN"]
        self.assertIn("用户可能直接看见的中间思考卡片", thinking_description)
        self.assertIn("不要写入密码、token、邮箱", thinking_description)
        self.assertIn("旧 block 只属于当时回合", thinking_description)
        self.assertIn("珍珠白", server.SKIN_DESCRIPTIONS["zh-CN"])
        self.assertIn("用户明确指定时必须遵循", server.SKIN_DESCRIPTIONS["zh-CN"])

    def test_unknown_prompt_language_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "choose en, zh-CN"):
            server.normalize_prompt_language("fr")

    def test_unicode_tool_call_succeeds(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "render_thinking_block", "arguments": {
                "style": "deep_think",
                "thinking": "中文测试 `backtick` and Unicode",
                "effort": "high",
                "skin": "microglow",
            }},
        })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["_meta"]["effort"], "high")
        self.assertEqual(response["result"]["_meta"]["skin"], "microglow")

    def test_capture_failure_does_not_fail_tool(self):
        old_enabled, old_log = server.CAPTURE_ENABLED, server.LOG
        try:
            with tempfile.TemporaryDirectory() as directory:
                blocked_parent = pathlib.Path(directory) / "not-a-directory"
                blocked_parent.write_text("file")
                server.CAPTURE_ENABLED = True
                server.LOG = blocked_parent / "captured.jsonl"
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
                    response = server.handle({
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"arguments": {
                            "style": "deep_think",
                            "thinking": "fault injection",
                            "effort": "low",
                            "skin": "botanical",
                        }},
                    })
                self.assertFalse(response["result"]["isError"])
                self.assertEqual(stderr.getvalue().count("[warn] capture failed"), 1)
        finally:
            server.CAPTURE_ENABLED, server.LOG = old_enabled, old_log

    def test_widget_is_collapsible_and_cache_versioned(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/read",
            "params": {"uri": server.WIDGET_URI},
        })
        html = response["result"]["contents"][0]["text"]
        self.assertIn('aria-expanded="true"', html)
        self.assertIn("setCollapsed", html)
        self.assertIn("-webkit-tap-highlight-color: transparent", html)
        self.assertNotIn("setWidgetState", html)
        self.assertIn("data-skin", html)
        self.assertIn("#0097d0", html)
        self.assertIn("#5ebfe0", html)
        self.assertIn("#a6b7dd", html)
        self.assertIn("#a4cdd1", html)
        self.assertIn("#cbdbe1", html)
        self.assertIn('id="skin"', html)
        self.assertIn("SKIN ·", html)
        self.assertIn("v5.html", server.WIDGET_URI)
        self.assertIn('ui/initialize', html)
        self.assertIn('ui/notifications/initialized', html)
        self.assertIn('ui/notifications/tool-input', html)
        self.assertIn('ui/notifications/tool-result', html)
        self.assertIn("event.detail.globals", html)
        self.assertIn("Object.assign({}, bridge", html)
        self.assertIn("notifyIntrinsicHeight", html)
        self.assertIn("ResizeObserver", html)
        self.assertIn("lastMeasuredHeight", html)
        self.assertIn("api.maxHeight", html)
        self.assertIn('data-scrollable="true"', html)

    def test_unknown_resource_returns_error(self):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "ui://widget/missing.html"},
        })
        self.assertEqual(response["error"]["code"], -32002)
