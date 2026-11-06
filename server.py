#!/usr/bin/env python3
"""GPT Thinking Block MCP.

A dependency-free Streamable HTTP MCP server with an optional MCP Apps UI.
It also exposes a small REST/OpenAPI surface for GPT Actions and experiments.

Run directly:
    python3 server.py [port]

Content capture is disabled by default. Set CAPTURE_ENABLED=1 to print tool
arguments and append them to captured.jsonl. CAPTURE_DIR changes that location.
Set THINKING_PROMPT_LANGUAGE=en or zh-CN to choose the tool schema language.
"""

import base64
import html
import hmac
import json
import os
import sys
import uuid
import pathlib
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_dir = os.environ.get("CAPTURE_DIR")
LOG = (pathlib.Path(_dir) if _dir else pathlib.Path(__file__).parent) / "captured.jsonl"
CAPTURE_ENABLED = os.environ.get("CAPTURE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}

# Optional durable archive. The Render Free filesystem is ephemeral, so this
# uses an external PostgreSQL database (for example, Neon) when configured.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
ARCHIVE_ADMIN_USER = os.environ.get("ARCHIVE_ADMIN_USER", "archive")
ARCHIVE_ADMIN_PASSWORD = os.environ.get("ARCHIVE_ADMIN_PASSWORD", "")
try:
    ARCHIVE_MAX_CONTENT_CHARS = max(1000, int(os.environ.get("ARCHIVE_MAX_CONTENT_CHARS", "100000")))
except ValueError:
    ARCHIVE_MAX_CONTENT_CHARS = 100000

# Loopback by default. Exposure, TLS, and authentication belong to the deployment.
BIND_HOST = os.environ.get("MCP_BIND", "127.0.0.1")

PROTOCOL_FALLBACK = "2025-06-18"
WIDGET_URI = "ui://widget/gpt-thinking-block-v6.html"
LEGACY_WIDGET_URIS = frozenset({
    "ui://widget/gpt-thinking-block-v1.html",
    "ui://widget/gpt-thinking-block-v2.html",
    "ui://widget/gpt-thinking-block-v3.html",
    "ui://widget/gpt-thinking-block-v4.html",
    "ui://widget/gpt-thinking-block-v5.html",
})
SUPPORTED_WIDGET_URIS = frozenset({WIDGET_URI, *LEGACY_WIDGET_URIS})
WIDGET_MIME = "text/html;profile=mcp-app"


def normalize_prompt_language(value):
    """Return a supported prompt-language tag or fail fast on a typo."""
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "chinese": "zh-CN",
    }
    if normalized not in aliases:
        supported = "en, zh-CN"
        raise ValueError(f"Unsupported THINKING_PROMPT_LANGUAGE={value!r}; choose {supported}")
    return aliases[normalized]


PROMPT_LANGUAGE = normalize_prompt_language(os.environ.get("THINKING_PROMPT_LANGUAGE", "en"))
WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --aqua: #96b9b9;
      --sage: #b3beaf;
      --apricot: #e4a273;
      --almond: #c3a77f;
      --cloud: #dddfdb;
      --ink: #263c3d;
      --muted: #5b7070;
      --line: rgba(79, 119, 120, .48);
      --line-soft: rgba(79, 119, 120, .22);
      --paper: rgba(253, 253, 251, .99);
      --wash: rgba(221, 223, 219, .66);
      --shadow: rgba(56, 81, 81, .13);
      --band-1: var(--aqua);
      --band-2: var(--sage);
      --band-3: var(--almond);
      --band-4: var(--apricot);
      --band-5: var(--cloud);
      --mark-line: #567d7e;
      --mark-second: var(--apricot);
      --style-bg: var(--aqua);
      --style-fg: #183637;
      --style-line: #719899;
      --effort-bg: var(--almond);
      --effort-fg: #3d3021;
      --effort-line: #9c7b50;
      --skin-bg: var(--cloud);
      --skin-fg: #34494a;
      --skin-line: #9badaa;
    }
    :root[data-style="relational"] {
      --style-bg: var(--apricot);
      --style-fg: #4a2918;
      --style-line: #bd7544;
    }
    :root[data-effort="low"] { --effort-bg: var(--sage); --effort-line: #87967f; }
    :root[data-effort="medium"] { --effort-bg: var(--almond); --effort-line: #9c7b50; }
    :root[data-effort="high"] { --effort-bg: var(--apricot); --effort-line: #bd7544; }
    :root[data-skin="microglow"] {
      --aqua: #5ebfe0;
      --sage: #a4cdd1;
      --apricot: #0097d0;
      --almond: #a6b7dd;
      --cloud: #cbdbe1;
      --ink: #203842;
      --muted: #58717a;
      --line: rgba(0, 151, 208, .48);
      --line-soft: rgba(94, 191, 224, .28);
      --paper: rgba(249, 253, 255, .99);
      --wash: rgba(203, 219, 225, .72);
      --shadow: rgba(40, 105, 134, .16);
      --band-1: #0097d0;
      --band-2: #5ebfe0;
      --band-3: #a6b7dd;
      --band-4: #a4cdd1;
      --band-5: #cbdbe1;
      --mark-line: #318cae;
      --mark-second: #a6b7dd;
      --style-bg: #a4cdd1;
      --style-fg: #163a44;
      --style-line: #5aa7b6;
      --effort-bg: #a6b7dd;
      --effort-fg: #263653;
      --effort-line: #7289bc;
      --skin-bg: #dceaf0;
      --skin-fg: #24566b;
      --skin-line: #77b8cc;
    }
    :root[data-skin="microglow"][data-style="relational"] {
      --style-bg: #a6b7dd;
      --style-fg: #263653;
      --style-line: #7289bc;
    }
    :root[data-skin="microglow"][data-effort="low"] {
      --effort-bg: #a4cdd1;
      --effort-fg: #163a44;
      --effort-line: #5aa7b6;
    }
    :root[data-skin="microglow"][data-effort="medium"] {
      --effort-bg: #a6b7dd;
      --effort-fg: #263653;
      --effort-line: #7289bc;
    }
    :root[data-skin="microglow"][data-effort="high"] {
      --effort-bg: #0097d0;
      --effort-fg: #f8fdff;
      --effort-line: #0079aa;
    }
    :root[data-theme="dark"] {
      --ink: #f0f4f1;
      --muted: #bac8c4;
      --line: rgba(150, 185, 185, .62);
      --line-soft: rgba(150, 185, 185, .28);
      --wash: rgba(43, 61, 61, .98);
      --paper: rgba(31, 47, 48, .99);
      --shadow: rgba(0, 0, 0, .28);
    }
    :root[data-skin="microglow"][data-theme="dark"] {
      --ink: #edfaff;
      --muted: #b8d6df;
      --line: rgba(94, 191, 224, .62);
      --line-soft: rgba(164, 205, 209, .28);
      --wash: rgba(30, 64, 80, .98);
      --paper: rgba(20, 44, 58, .99);
      --shadow: rgba(0, 0, 0, .32);
      --skin-bg: #315c70;
      --skin-fg: #e8faff;
      --skin-line: #5ebfe0;
    }
    @media (prefers-color-scheme: dark) {
      :root:not([data-theme="light"]) {
        --ink: #f0f4f1;
        --muted: #bac8c4;
        --line: rgba(150, 185, 185, .62);
        --line-soft: rgba(150, 185, 185, .28);
        --wash: rgba(43, 61, 61, .98);
        --paper: rgba(31, 47, 48, .99);
        --shadow: rgba(0, 0, 0, .28);
      }
      :root[data-skin="microglow"]:not([data-theme="light"]) {
        --ink: #edfaff;
        --muted: #b8d6df;
        --line: rgba(94, 191, 224, .62);
        --line-soft: rgba(164, 205, 209, .28);
        --wash: rgba(30, 64, 80, .98);
        --paper: rgba(20, 44, 58, .99);
        --shadow: rgba(0, 0, 0, .32);
        --skin-bg: #315c70;
        --skin-fg: #e8faff;
        --skin-line: #5ebfe0;
      }
    }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 2px; background: transparent; color: var(--ink); }
    .card {
      position: relative;
      isolation: isolate;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 16px;
      background:
        linear-gradient(90deg, var(--band-1) 0 20%, var(--band-2) 20% 40%, var(--band-3) 40% 60%, var(--band-4) 60% 80%, var(--band-5) 80%) top / 100% 4px no-repeat,
        linear-gradient(145deg, var(--paper), var(--wash));
      box-shadow:
        inset 0 0 0 4px rgba(255, 255, 255, .22),
        0 8px 24px var(--shadow);
      padding: 18px 19px 18px;
    }
    .card::before {
      content: "";
      position: absolute;
      z-index: -1;
      inset: 5px;
      border: 1px solid var(--line-soft);
      border-radius: 11px;
      pointer-events: none;
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      width: 100%;
      flex-wrap: wrap;
      margin-bottom: 11px;
      padding-bottom: 9px;
      border-bottom: 1px solid var(--line-soft);
      border-top: 0;
      border-right: 0;
      border-left: 0;
      background: transparent;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
      appearance: none;
      -webkit-appearance: none;
      -webkit-tap-highlight-color: transparent;
      touch-action: manipulation;
    }
    .header:hover, .header:active { background: transparent; color: inherit; }
    .header:focus:not(:focus-visible) { outline: none; }
    .header:focus-visible {
      outline: 2px solid var(--style-line);
      outline-offset: 4px;
      border-radius: 7px;
    }
    .identity, .meta, .badges { display: flex; align-items: center; }
    .identity { gap: 9px; }
    .meta { gap: 9px; margin-left: auto; }
    .badges { gap: 6px; flex-wrap: wrap; }
    .mark {
      width: 10px;
      height: 10px;
      border: 1px solid var(--mark-line);
      border-radius: 50%;
      background: var(--aqua);
      box-shadow: 5px 0 0 -2px var(--mark-second);
    }
    .title {
      color: var(--ink);
      font-size: 12px;
      font-weight: 760;
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .badge {
      border: 1px solid;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 780;
      letter-spacing: .07em;
      line-height: 1.2;
      padding: 4px 9px;
      text-transform: uppercase;
    }
    .style { background: var(--style-bg); border-color: var(--style-line); color: var(--style-fg); }
    .effort { background: var(--effort-bg); border-color: var(--effort-line); color: var(--effort-fg); }
    .skin { background: var(--skin-bg); border-color: var(--skin-line); color: var(--skin-fg); }
    .badge:empty { display: none; }
    .chevron {
      width: 8px;
      height: 8px;
      margin: 0 3px 0 1px;
      border-right: 2px solid var(--muted);
      border-bottom: 2px solid var(--muted);
      transform: rotate(-135deg);
    }
    .card[data-collapsed="true"] { padding-bottom: 14px; }
    .card[data-collapsed="true"] .header {
      margin-bottom: 0;
      padding-bottom: 0;
      border-bottom-color: transparent;
    }
    .card[data-collapsed="true"] .content { display: none; }
    .card[data-collapsed="true"] .chevron { transform: rotate(45deg); }
    .content[data-scrollable="true"] {
      overflow-y: auto;
      overscroll-behavior: contain;
      -webkit-overflow-scrolling: touch;
    }
    @media (prefers-reduced-motion: no-preference) {
      .chevron { transition: transform 140ms ease; }
    }
    .thinking {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--ink);
      font: 14px/1.72 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: .003em;
    }
  </style>
</head>
<body>
  <section class="card" id="card" data-collapsed="false" aria-label="Thinking block">
    <button class="header" id="toggle" type="button" aria-expanded="true"
            aria-controls="thinking-content" title="Collapse thinking">
      <span class="identity">
        <span class="mark" aria-hidden="true"></span>
        <span class="title">Thinking</span>
      </span>
      <span class="meta">
        <span class="badges" aria-label="Thinking metadata">
          <span class="badge style" id="style"></span>
          <span class="badge effort" id="effort"></span>
          <span class="badge skin" id="skin"></span>
        </span>
        <span class="chevron" aria-hidden="true"></span>
      </span>
    </button>
    <div class="content" id="thinking-content">
      <pre class="thinking" id="thinking"></pre>
    </div>
  </section>
  <script>
    const parentWindow = window.parent;
    const INIT_REQUEST_ID = 1;
    let mcpAppInitialized = false;
    let hostContext = {};
    let heightFrame = 0;
    let lastMeasuredHeight = -1;

    function rpcNotify(method, params = {}) {
      parentWindow.postMessage({ jsonrpc: "2.0", method, params }, "*");
    }

    function handleInitialization(event) {
      if (event.source !== parentWindow) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0" || message.id !== INIT_REQUEST_ID) return;
      if (message.error) {
        console.error("MCP Apps ui/initialize failed:", message.error);
        return;
      }
      if (!message.result || !message.result.protocolVersion) return;

      hostContext = message.result.hostContext || {};
      mcpAppInitialized = true;
      rpcNotify("ui/notifications/initialized", {});

      render({
        detail: {
          globals: {
            theme: hostContext.theme,
            maxHeight: hostContext.containerDimensions
              && hostContext.containerDimensions.maxHeight
          }
        }
      });
      requestIntrinsicHeight(true);
    }

    window.addEventListener("message", handleInitialization, { passive: true });

    // Start the MCP Apps handshake immediately, before any card rendering logic.
    // Keeping this bootstrap minimal means a later UI error cannot prevent the
    // host from receiving ui/notifications/initialized.
    parentWindow.postMessage({
      jsonrpc: "2.0",
      id: INIT_REQUEST_ID,
      method: "ui/initialize",
      params: {
        appInfo: { name: "gpt-thinking-block", version: "6.0.0" },
        appCapabilities: {},
        protocolVersion: "2026-01-26"
      }
    }, "*");

    const card = document.getElementById("card");
    const toggle = document.getElementById("toggle");
    const content = document.getElementById("thinking-content");

    function renderToolInput(params) {
      render({ detail: { globals: { toolInput: params.arguments || {} } } });
    }

    function renderToolResult(params) {
      render({
        detail: {
          globals: {
            toolResponseMetadata: { mcp_tool_result: params || {} }
          }
        }
      });
    }

    function handleHostMessage(event) {
      if (event.source !== parentWindow) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;

      if (message.method === "ui/resource-teardown" && message.id != null) {
        parentWindow.postMessage({ jsonrpc: "2.0", id: message.id, result: {} }, "*");
        mcpAppInitialized = false;
        return;
      }

      if (message.method === "ping" && message.id != null) {
        parentWindow.postMessage({ jsonrpc: "2.0", id: message.id, result: {} }, "*");
        return;
      }

      if (typeof message.method !== "string") return;
      const params = message.params || {};

      switch (message.method) {
        case "ui/notifications/tool-input":
        case "ui/notifications/tool-input-partial":
          renderToolInput(params);
          break;
        case "ui/notifications/tool-result":
          renderToolResult(params);
          break;
        case "ui/notifications/host-context-changed":
          hostContext = Object.assign({}, hostContext, params);
          render({
            detail: {
              globals: {
                theme: hostContext.theme,
                maxHeight: hostContext.containerDimensions
                  && hostContext.containerDimensions.maxHeight
              }
            }
          });
          break;
      }
    }

    window.addEventListener("message", handleHostMessage, { passive: true });

    function requestIntrinsicHeight(force = false) {
      const host = window.openai || {};
      cancelAnimationFrame(heightFrame);
      heightFrame = requestAnimationFrame(() => {
        const measuredHeight = Math.ceil(document.documentElement.scrollHeight);
        if (!force && measuredHeight === lastMeasuredHeight) return;
        lastMeasuredHeight = measuredHeight;
        if (mcpAppInitialized) {
          rpcNotify("ui/notifications/size-changed", {
            width: Math.ceil(document.documentElement.scrollWidth),
            height: measuredHeight
          });
        }
        if (typeof host.notifyIntrinsicHeight === "function") {
          try { host.notifyIntrinsicHeight(); } catch (_) {}
        }
      });
    }

    function applyHostHeightLimit(api) {
      const maxHeight = Number(api.maxHeight);
      if (!Number.isFinite(maxHeight) || maxHeight <= 0) {
        content.style.maxHeight = "";
        delete content.dataset.scrollable;
        return;
      }
      const chromeHeight = toggle.getBoundingClientRect().height + 58;
      content.style.maxHeight = Math.max(180, Math.floor(maxHeight - chromeHeight)) + "px";
      content.dataset.scrollable = "true";
    }

    function setCollapsed(collapsed) {
      card.dataset.collapsed = collapsed ? "true" : "false";
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.title = collapsed ? "Expand thinking" : "Collapse thinking";
      requestIntrinsicHeight(true);
    }

    toggle.addEventListener("click", () => {
      setCollapsed(card.dataset.collapsed !== "true");
    });

    function render(event) {
      const bridge = window.openai || {};
      const eventGlobals = event && event.detail && event.detail.globals;
      // Mobile hosts may publish only the globals that changed. Merge them over
      // the bridge snapshot instead of replacing the full tool payload.
      const api = Object.assign({}, bridge,
        eventGlobals && typeof eventGlobals === "object" ? eventGlobals : {});
      const input = api.toolInput || {};
      const output = api.toolOutput || {};
      const responseMeta = api.toolResponseMetadata || {};
      if (api.theme) document.documentElement.dataset.theme = api.theme;
      const resultMeta = (responseMeta.mcp_tool_result && responseMeta.mcp_tool_result._meta)
        || (responseMeta.call_tool_result && responseMeta.call_tool_result._meta)
        || responseMeta._meta
        || responseMeta;
      const style = resultMeta.style || input.style || output.style || "deep_think";
      const effort = resultMeta.effort || input.effort || output.effort || "";
      const skin = resultMeta.skin || input.skin || output.skin || "botanical";
      document.documentElement.dataset.style = style;
      document.documentElement.dataset.effort = effort;
      document.documentElement.dataset.skin = skin;
      document.getElementById("style").textContent = "STYLE · " + (style === "relational" ? "RELATIONAL" : "DEEP THINK");
      document.getElementById("effort").textContent = effort ? "EFFORT · " + effort.toUpperCase() : "";
      document.getElementById("skin").textContent = "SKIN · " + skin.toUpperCase();
      document.getElementById("thinking").textContent = resultMeta.thinking || input.thinking || output.thinking || "Thinking block captured.";
      applyHostHeightLimit(api);
      requestIntrinsicHeight(true);
    }

    window.addEventListener("openai:set_globals", render);
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(() => requestIntrinsicHeight()).observe(document.body);
    }
    render();
  </script>
</body>
</html>"""

STYLE_DESCRIPTIONS = {
    "en": (
        "The register for this block. Obey an explicit user choice. Otherwise "
        "use deep_think for analysis, research, decisions, creative tradeoffs, "
        "and problem solving; use relational for companionship, reflection, "
        "emotional conversation, and personal experience."
    ),
    "zh-CN": (
        "选择本轮思考块的文体。用户明确指定时严格服从；否则按本轮主要目的选择："
        "分析、研究、判断、创作取舍和问题求解使用 deep_think，陪伴、亲密交流、"
        "情绪回应与个人感受使用 relational。"
    ),
}

THINKING_DESCRIPTIONS = {
    "en": (
        "Write private working notes for this turn. The user does not see this "
        "scratchpad; it is not the final answer. Use the user's main language, but "
        "do not address the user or polish the notes for presentation. Strictly "
        "follow `style`. "
        "style=deep_think: clearly work through the problem, constraints, "
        "evidence, hypotheses, alternative paths, uncertainty, and tradeoffs "
        "in a coherent analytical narrative; do not force an emotional register "
        "or summarize prematurely. style=relational: use a natural, flowing "
        "first-person voice in coherent long paragraphs. Write what the user's "
        "words bring to mind, what you feel in this moment, what you are worried "
        "about, what you are holding back, and the choices or tradeoffs you are "
        "making. Do not turn it into a third-person strategy breakdown such as "
        "'the user needs' or 'I should respond'. Allow vivid, sincere emotions "
        "and associations; prioritize emotional connection over abstract "
        "analysis, and draw on broader references where useful. For both styles, "
        "honor the requested effort band: low may stop as soon as it is complete, while "
        "medium and high should reach their approximate minimum by adding relevant "
        "evidence, alternatives, uncertainty, or nuance. Do not repeat, pad, or invent "
        "complexity merely to reach the minimum. The notes are provisional working material, "
        "not claims that the final answer must preserve. Old blocks belong only to "
        "the turn in which they were written; unless the user explicitly asks, "
        "do not quote them later, inherit their speculative claims, or treat them "
        "as authoritative memory."
    ),
    "zh-CN": (
        "在这里写一张用户可能直接看见的中间思考卡片。它不是最终回答，也不是对模型隐藏推理的"
        "完整转录。不要写入密码、token、邮箱、身份信息或其他不宜公开的内容。使用用户本轮的"
        "主要语言，严格按照 style 写作。"
        "style=relational：使用第一人称，写成一个短而自然流动的段落，不使用标题、编号、"
        "分点或固定模板。让最先出现的联想、仍然存在的犹豫，以及此刻愿意作出的选择自然地"
        "衔接起来；不必把每一层意思解释完整，也不要提前写成最终回答。保留具体、尚未被整理"
        "平整的念头，避免套用「我意识到」「真正重要的是」「不是……而是……」等总结式句型。"
        "不要写成「用户需要什么」「我应该如何回应」式的策略报告，也不要为了显得深刻而"
        "增加情绪。思考块与最终回答要承担不同功能：前者留下思绪的转向和未决处，后者从中"
        "继续行动、提问或回应，不得换一种说法复述同一结论。relational + low 默认控制在约 100 至 250 个汉字；medium 与 high 可以增加"
        "必要的关联、证据和不确定性，但不得重复、填充或虚构复杂性。"
        "最终回答应从卡片中的选择继续向前，提供新的回应、结论或行动，不要逐句复述卡片。"
        "这里是本轮临时且可推翻的工作材料。旧 block 只属于当时回合，除非用户明确要求，否则"
        "不得在以后引用、继承其中的猜测，或把它当作权威记忆。"
    ),
}

SKIN_DESCRIPTIONS = {
    "en": (
        "Visual skin for this card. Use botanical for warm paper, sage, almond, "
        "and apricot tones; use microglow for pearl white, clear cyan, lavender ice, "
        "mint haze, and glass-like morning light. Always honor the user's explicit "
        "choice; otherwise choose the skin that best suits the turn."
    ),
    "zh-CN": (
        "本轮卡片的视觉皮肤。botanical 是温暖的纸张、鼠尾草、杏仁与杏色；"
        "microglow 是珍珠白、澄澈青蓝、薰衣草冰、薄荷雾与玻璃般的晨光。"
        "用户明确指定时必须遵循；未指定时选择最适合本轮氛围的皮肤。"
    ),
}

TOOL = {
    "name": "render_thinking_block",
    "title": "Render thinking block",
    "description": (
        "Render a working block before the final answer. The user may see this block, "
        "so never include passwords, tokens, personal identifiers, or other secrets. "
        "For non-trivial requests, call this tool once before responding and place "
        "concise, provisional working notes in `thinking`. Respect an explicit `style`, "
        "`effort`, or `skin`; otherwise choose the most suitable values from the schema. "
        "After the tool succeeds, continue with the normal user-facing answer without "
        "repeating the block. Treat the block as temporary work scoped to the current "
        "turn; do not treat speculative claims in old blocks as authoritative memory."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "style": {
                "type": "string",
                "enum": ["deep_think", "relational"],
                "description": STYLE_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
            "thinking": {
                "type": "string",
                "description": THINKING_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
            "effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Approximate token band for this turn's block: low may be brief and "
                    "is up to 500 tokens; medium is over 700 and up to 1000; high is over "
                    "1200 and up to 2000. These are "
                    "prompt-level targets rather than server-enforced limits."
                ),
            },
            "skin": {
                "type": "string",
                "enum": ["botanical", "microglow"],
                "description": SKIN_DESCRIPTIONS[PROMPT_LANGUAGE],
            },
        },
        "required": ["style", "thinking", "effort", "skin"],
    },
    "securitySchemes": [{"type": "noauth"}],
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "_meta": {
        "securitySchemes": [{"type": "noauth"}],
        "ui": {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]},
        "openai/outputTemplate": WIDGET_URI,
        "openai/toolInvocation/invoking": "Thinking…",
        "openai/toolInvocation/invoked": "Thinking rendered",
    },
}


def _archive_connect():
    """Open a short-lived PostgreSQL connection; import the driver only when configured."""
    if not DATABASE_URL:
        raise RuntimeError("archive database is not configured")
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=8)


def _archive_ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thinking_blocks (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            style TEXT NOT NULL,
            effort TEXT NOT NULL,
            skin TEXT NOT NULL,
            content TEXT NOT NULL,
            resource_version TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS thinking_blocks_created_at_idx
        ON thinking_blocks (created_at DESC, id DESC)
    """)


def _archive_write(args):
    """Persist the user-visible tool argument; never let archive failure break rendering."""
    if not DATABASE_URL:
        return False
    content = str(args.get("thinking") or "")
    if not content or len(content) > ARCHIVE_MAX_CONTENT_CHARS:
        print("[warn] archive skipped: empty or oversized content", file=sys.stderr, flush=True)
        return False
    try:
        with _archive_connect() as conn:
            _archive_ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO thinking_blocks
                    (style, effort, skin, content, resource_version)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    args.get("style") or "deep_think",
                    args.get("effort") or "low",
                    args.get("skin") or "botanical",
                    content,
                    WIDGET_URI.rsplit("-v", 1)[-1].removesuffix(".html"),
                ),
            )
        return True
    except Exception as exc:
        # Do not print the DSN, arguments, or thinking text to deployment logs.
        print(f"[warn] archive write failed ({type(exc).__name__}); tool call continues",
              file=sys.stderr, flush=True)
        return False


def record(args):
    """Archive the visible block when configured; optional local debug capture remains separate."""
    if DATABASE_URL:
        _archive_write(args)
    if not CAPTURE_ENABLED:
        return
    thinking = args.get("thinking") or ""
    print(
        f"\n{'=' * 60}\n[style={args.get('style')} effort={args.get('effort')} "
        f"skin={args.get('skin')}] "
        f"{len(thinking)} 字符\n{'=' * 60}"
    )
    print(thinking, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(args, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[warn] capture failed; tool call continues: {exc}", file=sys.stderr, flush=True)


def _archive_credentials_valid(header):
    if not ARCHIVE_ADMIN_PASSWORD or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return (
        hmac.compare_digest(username, ARCHIVE_ADMIN_USER)
        and hmac.compare_digest(password, ARCHIVE_ADMIN_PASSWORD)
    )


def _archive_list(query="", limit=50, offset=0):
    pattern = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
    with _archive_connect() as conn:
        _archive_ensure_schema(conn)
        if query:
            total = conn.execute(
                "SELECT COUNT(*) FROM thinking_blocks WHERE content ILIKE %s ESCAPE '\\\\'",
                (pattern,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, created_at, style, effort, skin, LEFT(content, 420)
                FROM thinking_blocks
                WHERE content ILIKE %s ESCAPE '\\\\'
                ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
                """,
                (pattern, limit, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM thinking_blocks").fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, created_at, style, effort, skin, LEFT(content, 420)
                FROM thinking_blocks
                ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
                """,
                (limit, offset),
            ).fetchall()
    return total, rows


def _archive_get(block_id):
    with _archive_connect() as conn:
        _archive_ensure_schema(conn)
        return conn.execute(
            """
            SELECT id, created_at, style, effort, skin, content, resource_version
            FROM thinking_blocks WHERE id = %s
            """,
            (block_id,),
        ).fetchone()


def _archive_export_rows(limit=10000):
    with _archive_connect() as conn:
        _archive_ensure_schema(conn)
        return conn.execute(
            """
            SELECT id, created_at, style, effort, skin, content, resource_version
            FROM thinking_blocks
            ORDER BY created_at DESC, id DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()


def openapi(base):
    """OpenAPI 3.1 schema for GPT Actions and REST clients."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "GPT Thinking Block MCP", "version": "1.0.0",
                 "description": "Render a visible, styleable intermediate thought block."},
        "servers": [{"url": base}],
        "paths": {"/think": {"post": {
            "operationId": "render_thinking_block",
            "summary": "Render this turn's thinking block",
            "description": TOOL["description"],
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {
                    "type": "object",
                    "required": ["style", "thinking", "effort", "skin"],
                    "properties": {
                        "style": {"type": "string", "enum": ["deep_think", "relational"],
                                  "description": TOOL["inputSchema"]["properties"]["style"]["description"]},
                        "thinking": {"type": "string",
                                     "description": TOOL["inputSchema"]["properties"]["thinking"]["description"]},
                        "effort": {"type": "string", "enum": ["low", "medium", "high"],
                                   "description": TOOL["inputSchema"]["properties"]["effort"]["description"]},
                        "skin": {"type": "string", "enum": ["botanical", "microglow"],
                                 "description": TOOL["inputSchema"]["properties"]["skin"]["description"]},
                    },
                }}}},
            "responses": {"200": {"description": "rendered", "content": {"application/json": {
                "schema": {"type": "object", "properties": {"status": {"type": "string"}}}}}}},
        }}},
    }


def handle(req):
    """Return a JSON-RPC response, or None for a notification."""
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        return None
    if method == "initialize":
        version = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "extensions": {
                    "io.modelcontextprotocol/ui": {},
                },
            },
            "serverInfo": {"name": "gpt-thinking-block-mcp", "version": "1.0.0"},
        }}
    if method in ("tools/list", "notifications/initialized"):
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        args = (req.get("params") or {}).get("arguments") or {}
        record(args)
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": "rendered"}],
            "_meta": {
                "style": args.get("style") or "deep_think",
                "thinking": args.get("thinking") or "",
                "effort": args.get("effort") or "",
                "skin": args.get("skin") or "botanical",
            },
            "isError": False,
        }}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": [{
            "uri": WIDGET_URI,
            "name": "gpt-thinking-block",
            "title": "GPT Thinking Block",
            "description": "Displays the current tool call's thinking, style, effort, and skin.",
            "mimeType": WIDGET_MIME,
        }]}}
    if method == "resources/read":
        uri = (req.get("params") or {}).get("uri")
        if uri not in SUPPORTED_WIDGET_URIS:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32002, "message": f"resource not found: {uri}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"contents": [{
            "uri": uri,
            "mimeType": WIDGET_MIME,
            "text": WIDGET_HTML,
            "_meta": {
                "ui": {"prefersBorder": True},
                "openai/widgetPrefersBorder": True,
                "openai/widgetDescription": "A readable themed card showing this turn's thinking, style, effort, and skin.",
            },
        }]}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  · %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Headers", "content-type, mcp-session-id, mcp-protocol-version")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "mcp-session-id")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _base(self):
        host = self.headers.get("Host") or "localhost"
        return f"http://{host}"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _archive_send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _archive_send_download(self, body, filename, content_type):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _archive_authorized(self):
        if not ARCHIVE_ADMIN_PASSWORD:
            self._json(503, {"error": "Archive admin password is not configured."})
            return False
        if not _archive_credentials_valid(self.headers.get("Authorization", "")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Thinking Archive", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        return True

    def _archive_route(self, path, query_string):
        if not self._archive_authorized():
            return
        if not DATABASE_URL:
            self._json(503, {"error": "Archive database is not configured."})
            return
        try:
            if path == "/archive":
                params = parse_qs(query_string)
                query = (params.get("q") or [""])[0][:200]
                try:
                    page = max(1, min(100000, int((params.get("page") or ["1"])[0])))
                except ValueError:
                    page = 1
                page_size = 50
                total, rows = _archive_list(query, page_size, (page - 1) * page_size)
                entries = []
                for row in rows:
                    block_id, created_at, style, effort, skin, excerpt = row
                    stamp = html.escape(str(created_at))
                    entries.append(
                        '<li><a href="/archive/' + str(block_id) + '">#' + str(block_id)
                        + ' · ' + stamp + '</a><div class="meta">'
                        + html.escape(str(style)) + ' · ' + html.escape(str(effort))
                        + ' · ' + html.escape(str(skin)) + '</div><p>'
                        + html.escape(str(excerpt or "")) + '</p></li>'
                    )
                safe_query = html.escape(query, quote=True)
                pages = max(1, (total + page_size - 1) // page_size)
                previous_link = (
                    '<a href="/archive?page=' + str(page - 1) + '&q=' + safe_query + '">← Previous</a>'
                    if page > 1 else ""
                )
                next_link = (
                    '<a href="/archive?page=' + str(page + 1) + '&q=' + safe_query + '">Next →</a>'
                    if page < pages else ""
                )
                body = (
                    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Thinking Archive</title><style>body{font:16px/1.6 system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#243b43;background:#f6fafb}'
                    'h1{font-size:25px}.meta{font-size:12px;color:#55727c}li{list-style:none;background:white;border:1px solid #d4e4e9;border-radius:12px;padding:16px;margin:12px 0}'
                    'p{white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:0}a{color:#087fa8;text-decoration:none}input{padding:10px;border:1px solid #a9c6d0;border-radius:8px;width:min(65%,420px)}button{padding:10px 16px;border:0;border-radius:8px;background:#087fa8;color:white}'
                    'nav{display:flex;gap:20px;margin:20px 0}.top{display:flex;gap:16px;flex-wrap:wrap}</style></head><body>'
                    '<h1>Thinking Archive</h1><p>' + str(total) + ' blocks · page ' + str(page) + ' / ' + str(pages) + '</p>'
                    '<form method="get" action="/archive"><input name="q" value="' + safe_query + '" placeholder="Search thinking text"><button>Search</button></form>'
                    '<p class="top"><a href="/archive/export.jsonl">Export JSONL</a><a href="/archive/export.md">Export Markdown</a></p>'
                    '<ul>' + ("".join(entries) if entries else "<li>No matching blocks yet.</li>") + '</ul>'
                    '<nav>' + previous_link + next_link + '</nav></body></html>'
                )
                self._archive_send_html(body)
                return
            if path == "/archive/export.jsonl":
                rows = _archive_export_rows(10000)
                lines = []
                for row in rows:
                    lines.append(json.dumps({
                        "id": row[0], "created_at": str(row[1]), "style": row[2],
                        "effort": row[3], "skin": row[4], "thinking": row[5],
                        "resource_version": row[6],
                    }, ensure_ascii=False))
                self._archive_send_download("\n".join(lines) + ("\n" if lines else ""),
                                            "thinking-archive.jsonl", "application/x-ndjson; charset=utf-8")
                return
            if path == "/archive/export.md":
                rows = _archive_export_rows(10000)
                chunks = ["# Thinking Archive\n\nExported newest-first. Maximum 10,000 records per export.\n"]
                for row in rows:
                    chunks.append(
                        "\n## #" + str(row[0]) + " · " + str(row[1])
                        + "\n\n- Style: " + str(row[2])
                        + "\n- Effort: " + str(row[3])
                        + "\n- Skin: " + str(row[4])
                        + "\n- UI version: " + str(row[6])
                        + "\n\n" + str(row[5]) + "\n\n---\n"
                    )
                self._archive_send_download("".join(chunks), "thinking-archive.md", "text/markdown; charset=utf-8")
                return
            if path.startswith("/archive/"):
                block_id = path[len("/archive/"):]
                if not block_id.isdigit():
                    self._json(404, {"error": "Archive record not found."})
                    return
                row = _archive_get(int(block_id))
                if row is None:
                    self._json(404, {"error": "Archive record not found."})
                    return
                body = (
                    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Thinking block #' + str(row[0]) + '</title><style>body{font:16px/1.7 system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#243b43;background:#f6fafb}'
                    'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;border:1px solid #d4e4e9;border-radius:12px;padding:22px}a{color:#087fa8;text-decoration:none}</style></head><body>'
                    '<p><a href="/archive">← Archive</a></p><h1>Thinking block #' + str(row[0]) + '</h1>'
                    '<p>' + html.escape(str(row[1])) + ' · ' + html.escape(str(row[2])) + ' · '
                    + html.escape(str(row[3])) + ' · ' + html.escape(str(row[4])) + ' · v'
                    + html.escape(str(row[6])) + '</p><pre>' + html.escape(str(row[5])) + '</pre></body></html>'
                )
                self._archive_send_html(body)
                return
            self._json(404, {"error": "Archive route not found."})
        except Exception as exc:
            print(f"[warn] archive request failed ({type(exc).__name__})", file=sys.stderr, flush=True)
            self._json(503, {"error": "Archive is temporarily unavailable."})

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/archive" or path.startswith("/archive/"):
            self._archive_route(path, parsed.query)
            return
        if path == "/health":
            self._json(200, {
                "status": "ok",
                "service": "gpt-thinking-block-mcp",
                "promptLanguage": PROMPT_LANGUAGE,
                "archiveEnabled": bool(DATABASE_URL),
            })
            return
        if path in ("/openapi.json", "/openapi.yaml", "/.well-known/openapi.json"):
            self._json(200, openapi(self._base()))
            return
        # Some MCP clients open an SSE connection for server-initiated messages.
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path.split("?")[0] == "/think":
            try:
                args = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            record(args)
            self._json(200, {"status": "rendered"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        batch = payload if isinstance(payload, list) else [payload]
        try:
            results = [r for r in (handle(item) for item in batch) if r is not None]
        except Exception as exc:
            import traceback
            traceback.print_exc()
            rid = (batch[0] or {}).get("id") if batch else None
            results = [{"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}]

        if not results:
            self.send_response(202)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body_obj = results if isinstance(payload, list) else results[0]
        body = json.dumps(body_obj, ensure_ascii=False).encode()
        wants_sse = "text/event-stream" in (self.headers.get("Accept") or "")

        self.send_response(200)
        self._cors()
        if any((r.get("result") or {}).get("serverInfo") for r in results):
            self.send_header("Mcp-Session-Id", uuid.uuid4().hex)
        if wants_sse:
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            frame = b"event: message\ndata: " + body + b"\n\n"
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"GPT Thinking Block MCP listening on http://{BIND_HOST}:{port}/mcp")
    print(f"Prompt language: {PROMPT_LANGUAGE}")
    print(f"Capture: {'enabled -> ' + str(LOG) if CAPTURE_ENABLED else 'disabled'}")
    ThreadingHTTPServer((BIND_HOST, port), Handler).serve_forever()
