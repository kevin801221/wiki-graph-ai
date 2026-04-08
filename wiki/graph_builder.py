import json
import random
import re
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
EXCLUDED_FILES = {"index.md", "log.md"}


def parse_frontmatter(text: str) -> dict[str, object]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}

    data: dict[str, object] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [item.strip() for item in value[1:-1].split(",") if item.strip()]
            data[key.strip()] = items
        else:
            data[key.strip()] = value
    return data


def strip_frontmatter(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return text
    return text[match.end():]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate_text(text: str, limit: int = 280) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def extract_markdown_summary(text: str) -> str:
    body = strip_frontmatter(text)
    lines = body.splitlines()
    paragraph: list[str] = []
    started = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if started and paragraph:
                break
            continue
        if line.startswith("#") or line.startswith("```"):
            if started and paragraph:
                break
            continue
        if line.startswith("- ") or line.startswith("* "):
            if started and paragraph:
                break
            continue
        paragraph.append(line)
        started = True

    return truncate_text(" ".join(paragraph))


def extract_raw_summary(raw_path: Path) -> str:
    try:
        payload = json.loads(raw_path.read_text())
    except json.JSONDecodeError:
        return raw_path.name

    if not isinstance(payload, dict):
        return raw_path.name

    parts: list[str] = []
    for key in ("episode", "podcast_name", "date"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    insights = payload.get("insights")
    if isinstance(insights, list) and insights:
        first = insights[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())

    return truncate_text("｜".join(parts), limit=320)


def collect_graph(wiki_dir: Path) -> dict[str, list[dict[str, object]]]:
    pages: dict[str, dict[str, object]] = {}
    root_dir = wiki_dir.parent

    markdown_paths = [wiki_dir / "overview.md"]
    for folder in ("concepts", "entities", "sources", "synthesis"):
        markdown_paths.extend(sorted((wiki_dir / folder).glob("*.md")))

    for path in markdown_paths:
        if not path.exists() or path.name in EXCLUDED_FILES:
            continue

        text = path.read_text()
        frontmatter = parse_frontmatter(text)
        title = str(frontmatter.get("title") or path.stem)
        page_type = str(frontmatter.get("type") or path.parent.name.rstrip("s"))
        tags = frontmatter.get("tags") or []
        source_files = frontmatter.get("sources") or []
        links = sorted(set(LINK_RE.findall(text)))

        pages[title] = {
            "title": title,
            "type": page_type,
            "path": str(path.relative_to(wiki_dir.parent)),
            "tags": tags,
            "summary": extract_markdown_summary(text),
            "source_files": source_files,
            "links": links,
        }

    for raw_path in sorted((root_dir / "raw").glob("*.json")):
        raw_title = f"raw/{raw_path.name}"
        pages[raw_title] = {
            "title": raw_title,
            "type": "raw",
            "path": str(raw_path.relative_to(root_dir)),
            "tags": ["raw"],
            "summary": extract_raw_summary(raw_path),
            "source_files": [],
            "links": [],
        }

    nodes: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    inbound_count = {title: 0 for title in pages}

    def add_link(source: str, target: str) -> None:
        if source not in pages or target not in pages or source == target:
            continue_flag = True
        else:
            continue_flag = False
        if continue_flag:
            return
        links.append({"source": source, "target": target})
        inbound_count[target] += 1

    for title, page in pages.items():
        for target in page["links"]:
            add_link(title, target)
        for source_file in page["source_files"]:
            add_link(f"raw/{source_file}", title)

    for title, page in pages.items():
        outbound_count = sum(1 for link in links if link["source"] == title)
        nodes.append(
            {
                "id": title,
                "title": title,
                "type": page["type"],
                "path": page["path"],
                "tags": page["tags"],
                "summary": page["summary"],
                "inbound": inbound_count[title],
                "outbound": outbound_count,
                "degree": inbound_count[title] + outbound_count,
            }
        )

    nodes.sort(key=lambda item: (str(item["type"]), str(item["title"])))
    links.sort(key=lambda item: (str(item["source"]), str(item["target"])))
    return {"nodes": nodes, "links": links}


def render_html(graph: dict[str, list[dict[str, object]]]) -> str:
    graph_json = json.dumps(graph, ensure_ascii=False, indent=2)
    seed = random.Random(7).randint(1, 999999)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LLM Wiki General Graph</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f1e8;
      --panel: rgba(255, 252, 245, 0.9);
      --text: #1d1b18;
      --muted: #6a6258;
      --line: rgba(61, 54, 44, 0.18);
      --overview: #506d84;
      --concept: #c96834;
      --entity: #2f7c67;
      --source: #805ad5;
      --raw: #9a7b4f;
      --synthesis: #b2456e;
      --other: #7b6f61;
      --accent: #131313;
      --highlight: #f0b429;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Noto Serif TC", serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(201, 104, 52, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(80, 109, 132, 0.18), transparent 26%),
        linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
      overflow: hidden;
    }}

    #app {{
      position: relative;
      width: 100vw;
      height: 100vh;
    }}

    canvas {{
      width: 100%;
      height: 100%;
      display: block;
      cursor: grab;
    }}

    canvas.dragging {{
      cursor: grabbing;
    }}

    .panel {{
      position: absolute;
      top: 20px;
      left: 20px;
      width: min(340px, calc(100vw - 40px));
      padding: 18px 18px 16px;
      background: var(--panel);
      border: 1px solid rgba(29, 27, 24, 0.08);
      border-radius: 18px;
      backdrop-filter: blur(14px);
      box-shadow: 0 20px 50px rgba(44, 32, 18, 0.12);
    }}

    h1 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: -0.03em;
    }}

    .subtitle {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}

    .toolbar {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }}

    input {{
      width: 100%;
      border: 1px solid rgba(29, 27, 24, 0.12);
      border-radius: 999px;
      padding: 11px 14px;
      background: rgba(255, 255, 255, 0.82);
      font: inherit;
      color: var(--text);
    }}

    button {{
      border: 0;
      border-radius: 999px;
      padding: 11px 14px;
      background: var(--accent);
      color: white;
      font: inherit;
      cursor: pointer;
    }}

    button.secondary {{
      background: rgba(29, 27, 24, 0.08);
      color: var(--text);
    }}

    .controls {{
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}

    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.78);
      font-size: 13px;
    }}

    .toggle input {{
      width: auto;
      margin: 0;
    }}

    .range-control {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 9px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.78);
      font-size: 13px;
    }}

    .range-control input {{
      width: 128px;
      padding: 0;
      background: transparent;
      border: 0;
    }}

    .range-value {{
      min-width: 36px;
      text-align: right;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}

    .status {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}

    .stats {{
      display: flex;
      gap: 10px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}

    .stat {{
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.78);
      min-width: 92px;
    }}

    .stat-label {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .stat-value {{
      display: block;
      margin-top: 4px;
      font-size: 20px;
    }}

    .legend {{
      margin-top: 16px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
      font-size: 13px;
    }}

    .catalog {{
      margin-top: 16px;
      display: grid;
      gap: 12px;
    }}

    .catalog-group {{
      padding: 12px 12px 10px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid rgba(29, 27, 24, 0.08);
    }}

    .catalog-title {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin: 0 0 10px;
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .catalog-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .catalog-chip {{
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(29, 27, 24, 0.08);
      background: rgba(255, 255, 255, 0.92);
      font-size: 12px;
      color: var(--text);
    }}

    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .dot {{
      width: 11px;
      height: 11px;
      border-radius: 50%;
      display: inline-block;
      flex: 0 0 auto;
    }}

    .details {{
      position: absolute;
      right: 20px;
      top: 20px;
      width: min(320px, calc(100vw - 40px));
      padding: 18px;
      background: rgba(20, 18, 16, 0.84);
      color: white;
      border-radius: 18px;
      box-shadow: 0 16px 40px rgba(20, 18, 16, 0.24);
      backdrop-filter: blur(14px);
    }}

    .details h2 {{
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
    }}

    .details .meta {{
      margin-top: 8px;
      color: rgba(255, 255, 255, 0.72);
      font-size: 13px;
    }}

    .details .path {{
      margin-top: 12px;
      font-size: 13px;
      color: rgba(255, 255, 255, 0.84);
      word-break: break-all;
    }}

    .details .summary {{
      margin-top: 14px;
      font-size: 14px;
      line-height: 1.65;
      color: rgba(255, 255, 255, 0.94);
      white-space: pre-wrap;
    }}

    .details .tags,
    .details .neighbors {{
      margin-top: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}

    .chip {{
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      background: rgba(255, 255, 255, 0.12);
    }}

    .helper {{
      margin-top: 14px;
      color: rgba(255, 255, 255, 0.72);
      font-size: 13px;
      line-height: 1.5;
    }}

    .footer {{
      position: absolute;
      left: 20px;
      bottom: 18px;
      padding: 9px 12px;
      border-radius: 999px;
      background: rgba(255, 252, 245, 0.76);
      color: var(--muted);
      font-size: 12px;
      backdrop-filter: blur(12px);
    }}

    @media (max-width: 900px) {{
      .details {{
        top: auto;
        bottom: 18px;
        right: 18px;
      }}
    }}

    @media (max-width: 720px) {{
      .panel,
      .details {{
        position: absolute;
        left: 12px;
        right: 12px;
        width: auto;
      }}

      .details {{
        top: auto;
        bottom: 64px;
      }}
    }}
  </style>
</head>
<body>
  <div id="app">
    <canvas id="graph"></canvas>
    <section class="panel">
      <h1>LLM Wiki General Graph</h1>
      <div class="subtitle">以目前 wiki 頁面與 <code>[[頁面]]</code> 交叉連結生成的通用知識圖。拖曳空白處平移、滾輪縮放、點節點看關聯。</div>
      <div class="toolbar">
        <input id="search" type="search" placeholder="搜尋節點名稱">
        <button id="reset">重置</button>
      </div>
      <div class="controls">
        <label class="toggle"><input id="toggle-raw" type="checkbox">顯示 raw 節點</label>
        <label class="range-control">間距
          <input id="spacing" type="range" min="80" max="220" step="5" value="150">
          <span class="range-value" id="spacing-value">1.5x</span>
        </label>
        <button class="secondary" id="toggle-sim">暫停</button>
        <button class="secondary" id="reheat">重新排版</button>
      </div>
      <div class="status" id="sim-status">圖譜載入後會自動收斂並停止；raw 預設隱藏，避免節點過多造成畫面持續擾動。</div>
      <div class="stats">
        <div class="stat">
          <span class="stat-label">Visible Nodes</span>
          <span class="stat-value" id="node-count"></span>
        </div>
        <div class="stat">
          <span class="stat-label">Visible Links</span>
          <span class="stat-value" id="link-count"></span>
        </div>
        <div class="stat">
          <span class="stat-label">Seed</span>
          <span class="stat-value">{seed}</span>
        </div>
      </div>
      <div class="legend">
        <div class="legend-item"><span class="dot" style="background: var(--overview)"></span>overview</div>
        <div class="legend-item"><span class="dot" style="background: var(--concept)"></span>concept</div>
        <div class="legend-item"><span class="dot" style="background: var(--entity)"></span>entity</div>
        <div class="legend-item"><span class="dot" style="background: var(--source)"></span>source</div>
        <div class="legend-item"><span class="dot" style="background: var(--raw)"></span>raw</div>
        <div class="legend-item"><span class="dot" style="background: var(--synthesis)"></span>synthesis</div>
        <div class="legend-item"><span class="dot" style="background: var(--other)"></span>other</div>
      </div>
      <div id="node-catalog"></div>
    </section>
    <aside class="details" id="details"></aside>
    <div class="footer">輸出檔案：<code>wiki/graph.html</code></div>
  </div>

  <script>
    const graphData = {graph_json};

    const palette = {{
      overview: getComputedStyle(document.documentElement).getPropertyValue("--overview").trim(),
      concept: getComputedStyle(document.documentElement).getPropertyValue("--concept").trim(),
      entity: getComputedStyle(document.documentElement).getPropertyValue("--entity").trim(),
      source: getComputedStyle(document.documentElement).getPropertyValue("--source").trim(),
      raw: getComputedStyle(document.documentElement).getPropertyValue("--raw").trim(),
      synthesis: getComputedStyle(document.documentElement).getPropertyValue("--synthesis").trim(),
      other: getComputedStyle(document.documentElement).getPropertyValue("--other").trim(),
    }};

    const canvas = document.getElementById("graph");
    const ctx = canvas.getContext("2d");
    const details = document.getElementById("details");
    const searchInput = document.getElementById("search");
    const resetButton = document.getElementById("reset");
    const rawToggle = document.getElementById("toggle-raw");
    const spacingInput = document.getElementById("spacing");
    const spacingValue = document.getElementById("spacing-value");
    const simToggleButton = document.getElementById("toggle-sim");
    const reheatButton = document.getElementById("reheat");
    const simStatus = document.getElementById("sim-status");
    const nodeCount = document.getElementById("node-count");
    const linkCount = document.getElementById("link-count");
    const nodeCatalog = document.getElementById("node-catalog");

    const adjacency = new Map();
    graphData.nodes.forEach((node) => adjacency.set(node.id, new Set()));
    graphData.links.forEach((link) => {{
      adjacency.get(link.source)?.add(link.target);
      adjacency.get(link.target)?.add(link.source);
    }});

    const rng = (() => {{
      let value = {seed};
      return () => {{
        value = (value * 48271) % 2147483647;
        return value / 2147483647;
      }};
    }})();

    function buildNodeCatalogHtml() {{
      const grouped = new Map();
      for (const node of graphData.nodes) {{
        const type = node.type || "other";
        if (!grouped.has(type)) {{
          grouped.set(type, []);
        }}
        grouped.get(type).push(node.title);
      }}

      const order = ["overview", "synthesis", "concept", "entity", "source", "raw", "other"];
      return `
        <div class="catalog">
          ${{order.map((type) => {{
            const titles = (grouped.get(type) || []).slice(0, 8);
            if (!titles.length) return "";
            return `
              <div class="catalog-group">
                <div class="catalog-title">
                  <span>${{type}}</span>
                  <span>${{titles.length}} nodes</span>
                </div>
                <div class="catalog-chips">
                  ${{titles.map((title) => `<span class="catalog-chip">${{title}}</span>`).join("")}}
                </div>
              </div>
            `;
          }}).join("")}}
        </div>
      `;
    }}

    const TYPE_ORDER = ["overview", "synthesis", "concept", "source", "entity", "raw", "other"];
    const BASE_TYPE_CENTERS = {{
      overview: {{ x: 0, y: -260 }},
      synthesis: {{ x: 260, y: -180 }},
      concept: {{ x: -260, y: -120 }},
      source: {{ x: 300, y: 80 }},
      entity: {{ x: -80, y: 220 }},
      raw: {{ x: 520, y: 260 }},
      other: {{ x: 0, y: 0 }},
    }};

    const allNodes = graphData.nodes.map((node) => ({{
      ...node,
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      radius: 8 + Math.min(node.degree || 0, 10) * 1.2,
      visible: node.type !== "raw",
    }}));

    const nodeById = new Map(allNodes.map((node) => [node.id, node]));
    const allLinks = graphData.links
      .map((link) => {{
        const source = nodeById.get(link.source);
        const target = nodeById.get(link.target);
        if (!source || !target) return null;
        return {{ source, target }};
      }})
      .filter(Boolean);

    let visibleNodes = [];
    let visibleLinks = [];

    let width = 0;
    let height = 0;
    let devicePixelRatioValue = window.devicePixelRatio || 1;
    let scale = 1;
    let offsetX = 0;
    let offsetY = 0;
    let activeNode = null;
    let hoveredNode = null;
    let draggedNode = null;
    let isPanning = false;
    let panStart = null;
    let simulationRunning = true;
    let animationQueued = false;
    let alpha = 1;
    let lowEnergyFrames = 0;
    let layoutSpacing = 1.5;

    function formatSpacingValue() {{
      spacingValue.textContent = `${{layoutSpacing.toFixed(1)}}x`;
    }}

    function typeCenter(type) {{
      const center = BASE_TYPE_CENTERS[type] || BASE_TYPE_CENTERS.other;
      return {{
        x: center.x * layoutSpacing,
        y: center.y * layoutSpacing,
      }};
    }}

    function updateStats() {{
      nodeCount.textContent = visibleNodes.length;
      linkCount.textContent = visibleLinks.length;
    }}

    function updateSimulationStatus() {{
      if (simulationRunning) {{
        simStatus.textContent = `模擬中，顯示 ${{visibleNodes.length}} 個節點與 ${{visibleLinks.length}} 條連線；收斂後會自動停止。`;
        simToggleButton.textContent = "暫停";
      }} else {{
        simStatus.textContent = `圖譜已靜止，顯示 ${{visibleNodes.length}} 個節點與 ${{visibleLinks.length}} 條連線；目前間距為 ${{layoutSpacing.toFixed(1)}}x，可拖曳、縮放，或按「重新排版」再跑一次。`;
        simToggleButton.textContent = "啟動";
      }}
    }}

    function visibleNodeSet() {{
      return new Set(visibleNodes.map((node) => node.id));
    }}

    function rebuildVisibleGraph() {{
      visibleNodes = allNodes.filter((node) => node.visible);
      const visibleIds = visibleNodeSet();
      visibleLinks = allLinks.filter((link) => visibleIds.has(link.source.id) && visibleIds.has(link.target.id));
      if (activeNode && !activeNode.visible) {{
        setActiveNode(null);
      }}
      if (hoveredNode && !hoveredNode.visible) {{
        hoveredNode = null;
      }}
      updateStats();
      updateSimulationStatus();
    }}

    function placeNodesByType() {{
      const groups = new Map();
      for (const type of TYPE_ORDER) {{
        groups.set(type, []);
      }}
      for (const node of visibleNodes) {{
        const type = groups.has(node.type) ? node.type : "other";
        groups.get(type).push(node);
      }}

      for (const type of TYPE_ORDER) {{
        const group = groups.get(type) || [];
        const center = typeCenter(type);
        group.forEach((node, index) => {{
          const angle = (index / Math.max(group.length, 1)) * Math.PI * 2;
          const radius = (48 + Math.sqrt(index + 1) * 34 + rng() * 24) * layoutSpacing;
          node.x = center.x + Math.cos(angle) * radius;
          node.y = center.y + Math.sin(angle) * radius;
          node.vx = 0;
          node.vy = 0;
        }});
      }}
    }}

    function startSimulation() {{
      alpha = 1;
      lowEnergyFrames = 0;
      simulationRunning = true;
      updateSimulationStatus();
      scheduleFrame();
    }}

    function stopSimulation() {{
      simulationRunning = false;
      for (const node of visibleNodes) {{
        node.vx = 0;
        node.vy = 0;
      }}
      updateSimulationStatus();
    }}

    function resize() {{
      width = canvas.clientWidth = window.innerWidth;
      height = canvas.clientHeight = window.innerHeight;
      devicePixelRatioValue = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * devicePixelRatioValue);
      canvas.height = Math.floor(height * devicePixelRatioValue);
      ctx.setTransform(devicePixelRatioValue, 0, 0, devicePixelRatioValue, 0, 0);
    }}

    function colorFor(type) {{
      return palette[type] || palette.other;
    }}

    function toScreen(node) {{
      return {{
        x: node.x * scale + width / 2 + offsetX,
        y: node.y * scale + height / 2 + offsetY,
      }};
    }}

    function toWorld(x, y) {{
      return {{
        x: (x - width / 2 - offsetX) / scale,
        y: (y - height / 2 - offsetY) / scale,
      }};
    }}

    function draw() {{
      ctx.clearRect(0, 0, width, height);

      for (const link of visibleLinks) {{
        const source = toScreen(link.source);
        const target = toScreen(link.target);
        const highlighted = activeNode && (link.source.id === activeNode.id || link.target.id === activeNode.id);
        ctx.beginPath();
        ctx.moveTo(source.x, source.y);
        ctx.lineTo(target.x, target.y);
        ctx.strokeStyle = highlighted ? "rgba(240, 180, 41, 0.85)" : "rgba(61, 54, 44, 0.18)";
        ctx.lineWidth = highlighted ? 2.2 : 1;
        ctx.stroke();
      }}

      for (const node of visibleNodes) {{
        const point = toScreen(node);
        const isActive = activeNode && activeNode.id === node.id;
        const isNeighbor = activeNode && adjacency.get(activeNode.id)?.has(node.id);
        const isHovered = hoveredNode && hoveredNode.id === node.id;
        const alpha = activeNode ? (isActive || isNeighbor ? 1 : 0.2) : 1;

        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.arc(point.x, point.y, node.radius * scale * 0.9, 0, Math.PI * 2);
        ctx.fillStyle = isActive ? palette.other : colorFor(node.type);
        ctx.fill();

        if (isHovered || isActive) {{
          ctx.lineWidth = 3;
          ctx.strokeStyle = "rgba(240, 180, 41, 0.95)";
          ctx.stroke();
        }}

        if (isActive || isHovered || scale > 1.2) {{
          ctx.globalAlpha = alpha;
          ctx.fillStyle = "#1d1b18";
          ctx.font = '12px "Avenir Next", "Noto Sans TC", sans-serif';
          ctx.fillText(node.title, point.x + 10, point.y - 10);
        }}
      }}

      ctx.globalAlpha = 1;
    }}

    function tickSimulation() {{
      if (!simulationRunning) {{
        return;
      }}

      let energy = 0;

      for (let i = 0; i < visibleNodes.length; i++) {{
        for (let j = i + 1; j < visibleNodes.length; j++) {{
          const a = visibleNodes[i];
          const b = visibleNodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distSq = dx * dx + dy * dy + 0.01;
          const force = (2600 * alpha) / distSq;
          const fx = (dx / Math.sqrt(distSq)) * force;
          const fy = (dy / Math.sqrt(distSq)) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;

          const minDistance = (a.radius + b.radius + 18) * layoutSpacing;
          const distance = Math.sqrt(distSq);
          if (distance < minDistance) {{
            const push = ((minDistance - distance) / Math.max(minDistance, 1)) * 0.22;
            const px = (dx / distance) * push;
            const py = (dy / distance) * push;
            a.vx -= px;
            a.vy -= py;
            b.vx += px;
            b.vy += py;
          }}
        }}
      }}

      for (const link of visibleLinks) {{
        const dx = link.target.x - link.source.x;
        const dy = link.target.y - link.source.y;
        const distance = Math.sqrt(dx * dx + dy * dy) || 1;
        const spring = (distance - 135 * layoutSpacing) * 0.0031 * alpha;
        const fx = (dx / distance) * spring;
        const fy = (dy / distance) * spring;
        link.source.vx += fx;
        link.source.vy += fy;
        link.target.vx -= fx;
        link.target.vy -= fy;
      }}

      for (const node of visibleNodes) {{
        if (draggedNode && draggedNode.id === node.id) {{
          node.vx = 0;
          node.vy = 0;
          continue;
        }}
        const center = typeCenter(node.type);
        node.vx += (center.x - node.x) * 0.0025 * alpha;
        node.vy += (center.y - node.y) * 0.0025 * alpha;
        node.vx *= 0.78;
        node.vy *= 0.78;
        node.x += node.vx;
        node.y += node.vy;
        energy += Math.abs(node.vx) + Math.abs(node.vy);
      }}

      alpha *= 0.97;
      if (alpha < 0.025 || energy < Math.max(0.35, visibleNodes.length * 0.02)) {{
        lowEnergyFrames += 1;
      }} else {{
        lowEnergyFrames = 0;
      }}

      if (lowEnergyFrames >= 18) {{
        stopSimulation();
      }}
    }}

    function frame() {{
      animationQueued = false;
      tickSimulation();
      draw();
      if (simulationRunning || draggedNode || isPanning) {{
        scheduleFrame();
      }}
    }}

    function scheduleFrame() {{
      if (animationQueued) {{
        return;
      }}
      animationQueued = true;
      requestAnimationFrame(frame);
    }}

    function nodeAtPosition(clientX, clientY) {{
      const world = toWorld(clientX, clientY);
      for (let i = visibleNodes.length - 1; i >= 0; i--) {{
        const node = visibleNodes[i];
        const dx = world.x - node.x;
        const dy = world.y - node.y;
        if (Math.sqrt(dx * dx + dy * dy) <= node.radius * 1.2) {{
          return node;
        }}
      }}
      return null;
    }}

    function setActiveNode(node) {{
      activeNode = node;
      renderDetails(node);
    }}

    function renderDetails(node) {{
      if (!node) {{
        details.innerHTML = `
          <h2>目前圖譜</h2>
          <div class="meta">選一個節點查看摘要、type、路徑、標籤與鄰居。</div>
          <div class="helper">現在的圖預設先顯示 wiki 主知識層；<code>raw</code> 節點預設隱藏，避免畫面過度擁擠。你說的「抽象知識」在這裡主要就是 <code>concept</code> 節點，不是只能放公司或人物。</div>
        `;
        return;
      }}

      const neighbors = [...(adjacency.get(node.id) || [])].sort((a, b) => a.localeCompare(b, "zh-Hant"));
      const tags = Array.isArray(node.tags) ? node.tags : [];
      details.innerHTML = `
        <h2>${{node.title}}</h2>
        <div class="meta">type: ${{node.type}} ｜ degree: ${{node.degree}} ｜ in: ${{node.inbound}} ｜ out: ${{node.outbound}}</div>
        <div class="summary">${{node.summary || '目前沒有可用摘要。'}}</div>
        <div class="path">${{node.path}}</div>
        <div class="tags">
          ${{tags.length ? tags.map((tag) => `<span class="chip">${{tag}}</span>`).join("") : '<span class="chip">無 tags</span>'}}
        </div>
        <div class="neighbors">
          ${{neighbors.length ? neighbors.map((title) => `<span class="chip">${{title}}</span>`).join("") : '<span class="chip">無連線</span>'}}
        </div>
      `;
    }}

    canvas.addEventListener("mousemove", (event) => {{
      const node = nodeAtPosition(event.clientX, event.clientY);
      hoveredNode = node;

      if (draggedNode) {{
        const world = toWorld(event.clientX, event.clientY);
        draggedNode.x = world.x;
        draggedNode.y = world.y;
        scheduleFrame();
      }} else if (isPanning && panStart) {{
        offsetX = event.clientX - panStart.x;
        offsetY = event.clientY - panStart.y;
        scheduleFrame();
      }} else {{
        scheduleFrame();
      }}
    }});

    canvas.addEventListener("mousedown", (event) => {{
      const node = nodeAtPosition(event.clientX, event.clientY);
      if (node) {{
        draggedNode = node;
      }} else {{
        isPanning = true;
        panStart = {{ x: event.clientX - offsetX, y: event.clientY - offsetY }};
        canvas.classList.add("dragging");
      }}
      scheduleFrame();
    }});

    window.addEventListener("mouseup", () => {{
      draggedNode = null;
      isPanning = false;
      panStart = null;
      canvas.classList.remove("dragging");
      scheduleFrame();
    }});

    canvas.addEventListener("click", (event) => {{
      const node = nodeAtPosition(event.clientX, event.clientY);
      setActiveNode(node);
      scheduleFrame();
    }});

    canvas.addEventListener("wheel", (event) => {{
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.08 : 0.92;
      scale = Math.max(0.45, Math.min(2.8, scale * factor));
      scheduleFrame();
    }}, {{ passive: false }});

    searchInput.addEventListener("input", () => {{
      const query = searchInput.value.trim().toLowerCase();
      if (!query) {{
        hoveredNode = null;
        scheduleFrame();
        return;
      }}
      const match = visibleNodes.find((node) => node.title.toLowerCase().includes(query));
      if (match) {{
        setActiveNode(match);
        hoveredNode = match;
        scheduleFrame();
      }}
    }});

    resetButton.addEventListener("click", () => {{
      scale = 1;
      offsetX = 0;
      offsetY = 0;
      hoveredNode = null;
      searchInput.value = "";
      setActiveNode(null);
      scheduleFrame();
    }});

    rawToggle.addEventListener("change", () => {{
      for (const node of allNodes) {{
        if (node.type === "raw") {{
          node.visible = rawToggle.checked;
        }}
      }}
      rebuildVisibleGraph();
      placeNodesByType();
      startSimulation();
    }});

    spacingInput.addEventListener("input", () => {{
      layoutSpacing = Number(spacingInput.value) / 100;
      formatSpacingValue();
      updateSimulationStatus();
      placeNodesByType();
      startSimulation();
    }});

    simToggleButton.addEventListener("click", () => {{
      if (simulationRunning) {{
        stopSimulation();
      }} else {{
        startSimulation();
      }}
      scheduleFrame();
    }});

    reheatButton.addEventListener("click", () => {{
      placeNodesByType();
      startSimulation();
    }});

    window.addEventListener("resize", () => {{
      resize();
      scheduleFrame();
    }});

    resize();
    formatSpacingValue();
    rebuildVisibleGraph();
    placeNodesByType();
    renderDetails(null);
    nodeCatalog.innerHTML = buildNodeCatalogHtml();
    startSimulation();
  </script>
</body>
</html>
"""


def build_html_file(wiki_dir: Path) -> Path:
    graph = collect_graph(wiki_dir)
    output_path = wiki_dir / "graph.html"
    output_path.write_text(render_html(graph))
    return output_path


if __name__ == "__main__":
    output = build_html_file(Path(__file__).parent)
    print(output)
