from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import graph_builder

TODAY = date.today().isoformat()
WIKI_DIRNAME = "wiki"
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
AUTO_BLOCK_RE = re.compile(
    r"\n<!-- AUTO-INGEST START -->[\s\S]*?<!-- AUTO-INGEST END -->\n?",
    re.MULTILINE,
)
RELATED_RE = re.compile(r"\n## 相關頁面\n[\s\S]*$", re.MULTILINE)
SOURCE_SKIP = {"_cross_summary.json"}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
KEYPOINT_PAIR_RE = re.compile(r"^([A-Za-z_\-\u4e00-\u9fff ]{2,24}):\s*(.+)$")

TYPE_TO_CONCEPT = {
    "strategy": "策略觀察",
    "macro": "總體觀察",
    "sector": "產業觀察",
    "stock": "個股觀察",
    "personal": "人物觀點",
    "technology": "技術主題",
    "policy": "法規政策",
}

PAIR_KEY_TO_CONCEPT = {
    "sector": "產業",
    "industry": "子產業",
    "theme": "主題",
    "topic": "主題",
    "technology": "技術",
    "policy": "政策",
    "region": "地區",
    "country": "地區",
}

SOURCE_ENTITY_TAGS = {
    "yfinance": ["來源", "資料源"],
}


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


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int = 180) -> str:
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def sanitize_filename(name: str) -> str:
    return name.replace("/", "／").replace(":", "：")


def read_existing_sources(root_dir: Path) -> set[str]:
    sources: set[str] = set()
    wiki_dir = root_dir / WIKI_DIRNAME
    if not wiki_dir.exists():
        return sources
    for path in wiki_dir.rglob("*.md"):
        if path.name in {"index.md", "log.md"}:
            continue
        frontmatter = parse_frontmatter(path.read_text())
        for source in frontmatter.get("sources", []):
            if isinstance(source, str):
                sources.add(source)
    return sources


def discover_pending_raw_files(root_dir: Path, processed: set[str] | None = None) -> list[Path]:
    processed = processed if processed is not None else read_existing_sources(root_dir)
    pending: list[Path] = []
    raw_dir = root_dir / "raw"
    if not raw_dir.exists():
        return pending
    for path in sorted(raw_dir.glob("*.json")):
        if path.name in SOURCE_SKIP or path.name in processed:
            continue
        pending.append(path)
    return pending


def parse_keypoint_pairs(key_points: list[object]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_point in key_points:
        point = str(raw_point).strip()
        match = KEYPOINT_PAIR_RE.match(point)
        if not match:
            continue
        key = match.group(1).strip().lower().replace(" ", "_")
        value = match.group(2).strip()
        if key and value:
            pairs[key] = value
    return pairs


def extract_named_items(raw_values: object) -> set[str]:
    items: set[str] = set()
    for raw_value in list(raw_values or []):
        text = str(raw_value).strip()
        if text:
            items.add(text)
    return items


def source_entity_name(podcast_name: str) -> str:
    text = podcast_name.strip()
    return text if text else "未知來源"


def infer_entity_title(ticker: str, pairs: dict[str, str]) -> str:
    company_name = pairs.get("company") or pairs.get("long_name") or pairs.get("name")
    if company_name:
        return str(company_name).strip()
    return ticker.strip()


def extract_entities_and_concepts(podcast_name: str, insight: dict[str, object]) -> tuple[set[str], set[str]]:
    entities = extract_named_items(insight.get("entities", []))
    concepts = extract_named_items(insight.get("concepts", []))
    key_points = [str(point) for point in insight.get("key_points", []) or []]
    pairs = parse_keypoint_pairs(key_points)

    insight_type = str(insight.get("type", "")).strip().lower()
    mapped_concept = TYPE_TO_CONCEPT.get(insight_type)
    if mapped_concept:
        concepts.add(mapped_concept)

    if podcast_name.strip():
        entities.add(source_entity_name(podcast_name))

    for ticker in insight.get("tickers", []) or []:
        ticker_text = str(ticker).strip()
        if ticker_text:
            entities.add(infer_entity_title(ticker_text, pairs))
            if TICKER_RE.fullmatch(ticker_text):
                entities.add(ticker_text)

    for key, label in PAIR_KEY_TO_CONCEPT.items():
        value = pairs.get(key)
        if value:
            concepts.add(f"{label}：{value}")

    return {item for item in entities if item}, {item for item in concepts if item}


def build_source_record(file_name: str, payload: dict[str, object]) -> dict[str, object]:
    insights = payload.get("insights", []) or []
    first_content = ""
    if insights and isinstance(insights[0], dict):
        first_content = str(insights[0].get("content", ""))
    summary = truncate(
        "｜".join(
            filter(
                None,
                [
                    str(payload.get("episode", file_name)).strip(),
                    str(payload.get("podcast_name", "")).strip(),
                    str(payload.get("date", TODAY)).strip(),
                    first_content.strip(),
                ],
            )
        ),
        limit=320,
    )
    return {
        "title": str(payload.get("episode", Path(file_name).stem)).strip() or Path(file_name).stem,
        "podcast_name": str(payload.get("podcast_name", "")).strip(),
        "date": str(payload.get("date", TODAY)).strip() or TODAY,
        "summary": summary,
        "sources": [file_name],
    }


def format_frontmatter(data: dict[str, object]) -> str:
    lines = ["---"]
    for key in ("title", "type", "tags", "created", "updated", "sources"):
        value = data[key]
        if isinstance(value, list):
            formatted = ", ".join(str(item) for item in value)
            lines.append(f"{key}: [{formatted}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def build_source_filename(file_name: str) -> str:
    return sanitize_filename(Path(file_name).stem) + ".md"


def get_page_path(root_dir: Path, page_type: str, title: str, source_file: str | None = None) -> Path:
    if page_type == "source" and source_file:
        return root_dir / WIKI_DIRNAME / "sources" / build_source_filename(source_file)
    folder_map = {
        "concept": "concepts",
        "entity": "entities",
        "source": "sources",
        "synthesis": "synthesis",
    }
    if page_type == "overview":
        return root_dir / WIKI_DIRNAME / "overview.md"
    folder = folder_map[page_type]
    return root_dir / WIKI_DIRNAME / folder / f"{sanitize_filename(title)}.md"


def strip_auto_sections(body: str) -> str:
    body = AUTO_BLOCK_RE.sub("\n", body)
    body = RELATED_RE.sub("", body)
    return body.strip()


def read_existing_page(path: Path) -> tuple[dict[str, object], str]:
    if not path.exists():
        return {}, ""
    text = path.read_text()
    frontmatter = parse_frontmatter(text)
    body = strip_frontmatter(text)
    return frontmatter, strip_auto_sections(body)


def ensure_heading(body: str, title: str) -> str:
    stripped = body.strip()
    if not stripped:
        return f"# {title}"
    if stripped.startswith("# "):
        return stripped
    return f"# {title}\n\n{stripped}"


def merge_lists(existing: list[str] | object, new_items: list[str]) -> list[str]:
    merged: list[str] = []
    for item in list(existing or []) + new_items:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def build_related_section(related_titles: list[str]) -> str:
    unique: list[str] = []
    for title in related_titles:
        text = str(title).strip()
        if text and text not in unique:
            unique.append(text)
    lines = ["## 相關頁面"]
    for title in unique[:18]:
        lines.append(f"- [[{title}]]")
    return "\n".join(lines)


def build_auto_block(notes: list[str], related_titles: list[str]) -> str:
    lines = ["<!-- AUTO-INGEST START -->", "## 來源補充"]
    if notes:
        lines.extend(f"- {note}" for note in notes[:18])
    else:
        lines.append("- 目前尚無自動補充內容。")
    lines.append("")
    lines.append(build_related_section(related_titles))
    lines.append("<!-- AUTO-INGEST END -->")
    return "\n".join(lines)


def infer_entity_tags(title: str, notes: list[str]) -> list[str]:
    if title in SOURCE_ENTITY_TAGS:
        return SOURCE_ENTITY_TAGS[title]
    if TICKER_RE.fullmatch(title):
        return ["股票代號", "標的"]
    if notes and any("sector:" in note.lower() or "industry:" in note.lower() for note in notes):
        return ["公司", "產業樣本"]
    return ["實體"]


def infer_entity_summary(title: str, notes: list[str]) -> str:
    if notes:
        return f"這是目前 wiki 中與「{title}」相關的實體頁。{normalize_space(notes[0].split('：', 1)[-1])}"
    return f"這是目前 wiki 中與「{title}」相關的實體頁，後續來源會持續整理到這裡。"


def infer_concept_summary(title: str, notes: list[str]) -> str:
    if notes:
        return f"這個概念整理目前來源中與「{title}」相關的觀察。{normalize_space(notes[0].split('：', 1)[-1])}"
    return f"這個概念整理目前來源中與「{title}」相關的觀察。"


def write_page(
    path: Path,
    page_type: str,
    title: str,
    tags: list[str],
    sources: list[str],
    manual_intro: str,
    notes: list[str],
    related_titles: list[str],
) -> None:
    existing_frontmatter, existing_body = read_existing_page(path)
    created = str(existing_frontmatter.get("created", TODAY))
    merged_tags = merge_lists(existing_frontmatter.get("tags", []), tags)
    merged_sources = merge_lists(existing_frontmatter.get("sources", []), sources)
    manual_body = existing_body if existing_body else ensure_heading(manual_intro, title)
    content = "\n\n".join(
        [
            format_frontmatter(
                {
                    "title": title,
                    "type": page_type,
                    "tags": merged_tags,
                    "created": created,
                    "updated": TODAY,
                    "sources": merged_sources,
                }
            ),
            manual_body.strip(),
            build_auto_block(notes, related_titles),
        ]
    ).strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def render_source_page(record: dict[str, object]) -> str:
    title = str(record["title"])
    concepts = list(record["concepts"])
    entities = list(record["entities"])
    bullets = list(record["bullets"])
    lines = [
        format_frontmatter(
            {
                "title": title,
                "type": "source",
                "tags": merge_lists([], list(record["tags"])),
                "created": TODAY,
                "updated": TODAY,
                "sources": list(record["sources"]),
            }
        ),
        f"# {title}",
        "",
        str(record["summary"]),
        "",
        "## 文件重點",
    ]
    lines.extend(f"- {bullet}" for bullet in bullets[:12])
    lines.extend(["", "## 核心概念"])
    lines.extend(f"- [[{name}]]" for name in concepts[:12])
    lines.extend(["", "## 核心實體"])
    lines.extend(f"- [[{name}]]" for name in entities[:12])
    lines.extend(["", build_related_section(concepts[:8] + entities[:8])])
    return "\n".join(lines).strip() + "\n"


def build_source_output(file_name: str, payload: dict[str, object]) -> tuple[dict[str, object], dict[str, list[str]], dict[str, list[str]]]:
    source = build_source_record(file_name, payload)
    title = str(source["title"])
    podcast_name = str(source["podcast_name"])
    concepts: set[str] = set(extract_named_items(payload.get("concepts", [])))
    entities: set[str] = set()
    if podcast_name:
        entities.add(source_entity_name(podcast_name))

    bullets: list[str] = []
    concept_notes: dict[str, list[str]] = defaultdict(list)
    entity_notes: dict[str, list[str]] = defaultdict(list)

    for insight in payload.get("insights", []) or []:
        if not isinstance(insight, dict):
            continue
        matched_entities, matched_concepts = extract_entities_and_concepts(podcast_name, insight)
        entities |= matched_entities
        concepts |= matched_concepts
        insight_summary = truncate(str(insight.get("content", "")), limit=150)

        concept_links = "、".join(f"[[{name}]]" for name in sorted(matched_concepts)[:3])
        entity_links = "、".join(f"[[{name}]]" for name in sorted(matched_entities)[:3])
        bullet_parts = [insight_summary]
        if concept_links:
            bullet_parts.append(f"概念：{concept_links}")
        if entity_links:
            bullet_parts.append(f"實體：{entity_links}")
        bullets.append("｜".join(part for part in bullet_parts if part))

        for concept in matched_concepts:
            note = f"[[{title}]]：{insight_summary}"
            if note not in concept_notes[concept]:
                concept_notes[concept].append(note)
        for entity in matched_entities:
            note = f"[[{title}]]：{insight_summary}"
            if note not in entity_notes[entity]:
                entity_notes[entity].append(note)

    source["concepts"] = sorted(concepts)
    source["entities"] = sorted(entities)
    source["bullets"] = bullets
    source["tags"] = merge_lists([], [podcast_name, "來源摘要"] + sorted(list(concepts))[:3])
    return source, concept_notes, entity_notes


def build_index_entry(path: Path) -> tuple[str, str, list[str], str]:
    frontmatter = parse_frontmatter(path.read_text())
    title = str(frontmatter.get("title", path.stem))
    page_type = str(frontmatter.get("type", path.parent.name.rstrip("s")))
    sources = [str(item) for item in frontmatter.get("sources", [])]
    summary = graph_builder.extract_markdown_summary(path.read_text())
    return title, page_type, sources, summary


def collect_unique_page_paths(wiki_dir: Path) -> dict[str, Path]:
    candidates = [wiki_dir / "overview.md"]
    for folder in ("concepts", "entities", "sources", "synthesis"):
        candidates.extend(sorted((wiki_dir / folder).glob("*.md")))

    chosen: dict[str, Path] = {}
    for path in candidates:
        if not path.exists():
            continue
        title = str(parse_frontmatter(path.read_text()).get("title", path.stem))
        existing = chosen.get(title)
        if existing is None or len(path.name) < len(existing.name):
            chosen[title] = path
    return chosen


def rebuild_index(root_dir: Path) -> None:
    wiki_dir = root_dir / WIKI_DIRNAME
    buckets: dict[str, list[tuple[str, list[str], str]]] = defaultdict(list)
    page_count = 0

    for path in collect_unique_page_paths(wiki_dir).values():
        title, page_type, sources, summary = build_index_entry(path)
        buckets[page_type].append((title, sources, summary))
        page_count += 1

    lines = ["# Wiki Index", "", f"最後更新：{TODAY}｜共 {page_count} 頁", "", "## 總覽（overview）", ""]
    for title, _, summary in buckets.get("overview", []):
        lines.append(f"- [[{title}]] — {truncate(summary, 40)}")

    section_map = [
        ("concept", "概念（concepts/）"),
        ("entity", "實體（entities/）"),
        ("source", "文件摘要（sources/）"),
        ("synthesis", "分析與洞察（synthesis/）"),
    ]
    for key, header in section_map:
        lines.extend(["", f"## {header}", ""])
        items = sorted(buckets.get(key, []), key=lambda item: item[0])
        if not items:
            lines.append("（尚無頁面）")
            continue
        for title, sources, summary in items:
            if key == "source":
                lines.append(f"- [[{title}]] — {TODAY} ingest")
            elif key == "concept":
                lines.append(f"- [[{title}]] — {truncate(summary, 40)}（來自 {len(sources)} 份來源）")
            elif key == "synthesis":
                lines.append(f"- [[{title}]] — {truncate(summary, 40)}")
            else:
                lines.append(f"- [[{title}]] — {truncate(summary, 40)}")

    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "index.md").write_text("\n".join(lines).strip() + "\n")


def rebuild_overview(root_dir: Path) -> None:
    wiki_dir = root_dir / WIKI_DIRNAME
    unique_pages = collect_unique_page_paths(wiki_dir)
    source_pages = [path for path in unique_pages.values() if parse_frontmatter(path.read_text()).get("type") == "source"]
    concept_pages = [path for path in unique_pages.values() if parse_frontmatter(path.read_text()).get("type") == "concept"]
    entity_pages = [path for path in unique_pages.values() if parse_frontmatter(path.read_text()).get("type") == "entity"]
    synthesis_pages = [path for path in unique_pages.values() if parse_frontmatter(path.read_text()).get("type") == "synthesis"]
    pending = discover_pending_raw_files(root_dir)

    lines = [
        format_frontmatter(
            {
                "title": "整體知識摘要",
                "type": "overview",
                "tags": ["總覽"],
                "created": TODAY,
                "updated": TODAY,
                "sources": [],
            }
        ),
        "# 整體知識摘要",
        "",
        f"本 wiki 目前完成 {len(source_pages)} 份來源摘要、{len(concept_pages)} 個概念頁、{len(entity_pages)} 個實體頁與 {len(synthesis_pages)} 個 synthesis 頁。",
        "",
        "## 目前狀態",
        "",
        f"- `raw/` 尚未處理檔案：{len(pending)} 份",
        f"- 目前已收錄來源：{ '、'.join(f'[[{parse_frontmatter(path.read_text()).get("title", path.stem)}]]' for path in source_pages[:8]) if source_pages else '尚無' }",
        f"- 目前主要概念：{ '、'.join(f'[[{parse_frontmatter(path.read_text()).get("title", path.stem)}]]' for path in concept_pages[:8]) if concept_pages else '尚無' }",
        "",
        build_related_section(
            [parse_frontmatter(path.read_text()).get("title", path.stem) for path in synthesis_pages[:4]]
            + [parse_frontmatter(path.read_text()).get("title", path.stem) for path in source_pages[:6]]
        ),
    ]
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "overview.md").write_text("\n".join(lines).strip() + "\n")


def append_log_entries(root_dir: Path, log_entries: list[str]) -> None:
    log_path = root_dir / WIKI_DIRNAME / "log.md"
    existing = log_path.read_text() if log_path.exists() else "# Wiki Log\n"
    existing_headers = set(re.findall(r"^## \[[^\]]+\] .+$", existing, re.MULTILINE))
    filtered_entries: list[str] = []
    for entry in log_entries:
        stripped = entry.strip()
        if not stripped:
            continue
        header = stripped.splitlines()[0]
        if header in existing_headers:
            continue
        filtered_entries.append(stripped)
    if not filtered_entries:
        return
    append_text = "\n\n".join(filtered_entries)
    log_path.write_text(existing.rstrip() + "\n\n" + append_text.strip() + "\n")


def create_synthesis_pages(root_dir: Path, source_records: list[dict[str, object]]) -> list[str]:
    if not source_records:
        return []

    wiki_dir = root_dir / WIKI_DIRNAME
    source_titles = [str(record["title"]) for record in source_records]
    concept_titles: list[str] = []
    for record in source_records:
        for concept in record["concepts"]:
            if concept not in concept_titles:
                concept_titles.append(str(concept))

    pages = {
        "跨來源主題綜整": {
            "tags": ["synthesis", "跨來源", "主題整理"],
            "sources": sorted({source for record in source_records for source in record["sources"]}),
            "body": [
                "# 跨來源主題綜整",
                "",
                "這頁整理目前已 ingest 來源中重複出現的主題，幫助快速看出哪些概念是高頻核心、哪些只是一次性提及。",
                "",
                "## 目前高頻概念",
                "",
            ] + [f"- [[{title}]]" for title in concept_titles[:12]] + [
                "",
                build_related_section(concept_titles[:8] + source_titles[:8]),
            ],
        },
        "來源比較摘要": {
            "tags": ["synthesis", "來源比較"],
            "sources": sorted({source for record in source_records for source in record["sources"]}),
            "body": [
                "# 來源比較摘要",
                "",
                "這頁把目前來源放在同一個視角下查看，方便比較不同來源各自關注的概念、實體與觀點密度。",
                "",
                "## 已收錄來源",
                "",
            ] + [f"- [[{title}]]" for title in source_titles[:16]] + [
                "",
                build_related_section(source_titles[:10] + concept_titles[:6]),
            ],
        },
    }

    log_entries: list[str] = []
    for title, data in pages.items():
        path = wiki_dir / "synthesis" / f"{sanitize_filename(title)}.md"
        text = "\n".join(
            [
                format_frontmatter(
                    {
                        "title": title,
                        "type": "synthesis",
                        "tags": data["tags"],
                        "created": TODAY,
                        "updated": TODAY,
                        "sources": data["sources"],
                    }
                )
            ]
            + data["body"]
        ).strip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        log_entries.append(
            "\n".join(
                [
                    f"## [{TODAY}] synthesis | {title}",
                    f"- 新增 wiki/synthesis/{sanitize_filename(title)}.md",
                    f"- 串聯 {len(data['sources'])} 份來源，整理跨來源觀察",
                ]
            )
        )
    return log_entries


def run_ingest(root_dir: Path) -> dict[str, int]:
    pending_files = discover_pending_raw_files(root_dir)
    target_files = pending_files or sorted(
        path for path in (root_dir / "raw").glob("*.json") if path.name not in SOURCE_SKIP
    )

    wiki_dir = root_dir / WIKI_DIRNAME
    for subdir in ("concepts", "entities", "sources", "synthesis"):
        (wiki_dir / subdir).mkdir(parents=True, exist_ok=True)

    source_records: list[dict[str, object]] = []
    concept_agg: dict[str, dict[str, object]] = defaultdict(lambda: {"sources": set(), "notes": [], "related": set()})
    entity_agg: dict[str, dict[str, object]] = defaultdict(lambda: {"sources": set(), "notes": [], "related": set()})
    log_entries: list[str] = []

    for raw_path in target_files:
        payload = json.loads(raw_path.read_text())
        source_record, concept_notes, entity_notes = build_source_output(raw_path.name, payload)
        source_records.append(source_record)

        source_path = get_page_path(root_dir, "source", str(source_record["title"]), raw_path.name)
        source_path.write_text(render_source_page(source_record))

        for concept, notes in concept_notes.items():
            concept_agg[concept]["sources"].add(raw_path.name)
            concept_agg[concept]["notes"].extend(note for note in notes if note not in concept_agg[concept]["notes"])
            concept_agg[concept]["related"].update(source_record["entities"])
            concept_agg[concept]["related"].add(source_record["title"])

        for entity, notes in entity_notes.items():
            entity_agg[entity]["sources"].add(raw_path.name)
            entity_agg[entity]["notes"].extend(note for note in notes if note not in entity_agg[entity]["notes"])
            entity_agg[entity]["related"].update(source_record["concepts"])
            entity_agg[entity]["related"].add(source_record["title"])

        concept_pages = sorted(source_record["concepts"])[:6]
        entity_pages = sorted(source_record["entities"])[:8]
        log_entries.append(
            "\n".join(
                [
                    f"## [{TODAY}] ingest | {source_record['title']}",
                    f"- 新增 sources/{source_path.name}",
                    f"- 更新概念頁：{ '、'.join(f'[[{name}]]' for name in concept_pages) if concept_pages else '無' }",
                    f"- 更新實體頁：{ '、'.join(f'[[{name}]]' for name in entity_pages) if entity_pages else '無' }",
                ]
            )
        )

    for title, data in concept_agg.items():
        path = get_page_path(root_dir, "concept", title)
        intro = "\n\n".join([f"# {title}", "", infer_concept_summary(title, data["notes"])])
        related = sorted(set(data["related"]) | {"整體知識摘要"})
        write_page(
            path=path,
            page_type="concept",
            title=title,
            tags=["概念"],
            sources=sorted(data["sources"]),
            manual_intro=intro,
            notes=data["notes"],
            related_titles=related,
        )

    for title, data in entity_agg.items():
        path = get_page_path(root_dir, "entity", title)
        tags = infer_entity_tags(title, data["notes"])
        intro = "\n\n".join([f"# {title}", "", infer_entity_summary(title, data["notes"])])
        related = sorted(set(data["related"]) | {"整體知識摘要"})
        write_page(
            path=path,
            page_type="entity",
            title=title,
            tags=tags,
            sources=sorted(data["sources"]),
            manual_intro=intro,
            notes=data["notes"],
            related_titles=related,
        )

    log_entries.extend(create_synthesis_pages(root_dir, source_records))
    rebuild_overview(root_dir)
    rebuild_index(root_dir)
    append_log_entries(root_dir, log_entries)

    return {
        "processed": len(target_files),
        "sources": len(source_records),
        "concepts": len(concept_agg),
        "entities": len(entity_agg),
    }


def main() -> None:
    result = run_ingest(Path(__file__).parent.parent)
    print(f"processed {result['processed']} raw files")
    print(f"created_or_updated_sources {result['sources']}")
    print(f"created_or_updated_concepts {result['concepts']}")
    print(f"created_or_updated_entities {result['entities']}")


if __name__ == "__main__":
    main()
