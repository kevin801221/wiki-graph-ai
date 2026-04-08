from __future__ import annotations

import copy
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import batch_ingest
from google import genai
from google.genai import types

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
KEYWORD_CONCEPTS = {
    "AI": [" ai ", "ai", "人工智慧", "llm", "machine learning", "gpu", "model training"],
    "半導體": ["半導體", "semiconductor", "chip", "晶片", "gpu", "cpu", "asic", "foundry"],
    "記憶體": ["記憶體", "memory", "dram", "nand", "flash", "hbm", "ssd"],
    "電動車": ["電動車", "electric vehicle", "ev", "battery", "自駕", "autonomous driving"],
    "散熱": ["散熱", "thermal", "cooling", "liquid cooling", "液冷", "heat sink"],
    "量子電腦": ["量子", "quantum", "qubit", "量子電腦"],
    "光通訊": ["光通訊", "optical", "coherent", "transceiver", "photonic"],
    "CPO": ["cpo", "共同封裝光學", "co-packaged optics"],
    "矽光子": ["矽光子", "silicon photonics", "矽光"],
    "雲端": ["cloud", "雲端", "datacenter", "data center"],
    "網路安全": ["cybersecurity", "security", "零信任", "威脅偵測", "endpoint"],
}
ENTITY_PAIR_KEYS = {
    "company",
    "name",
    "long_name",
    "product",
    "technology",
    "person",
    "organization",
    "event",
}
ALIAS_CONCEPT_PREFIXES = {
    "theme": "主題",
    "sector": "產業",
    "industry": "子產業",
    "technology": "技術",
    "region": "地區",
    "country": "地區",
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]{1,30}|[\u4e00-\u9fff]{2,12}")
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", re.IGNORECASE)

SYSTEM_PROMPT = """你是一個 LLM Wiki ingestion agent。你的任務是閱讀單一 raw JSON payload，將內容整理成可寫入 Markdown wiki 的 canonical concepts 與 entities。

規則：
1. 全部輸出使用繁體中文。
2. `entities` 是具體名詞：公司、人物、產品、技術、事件、組織、代號。
3. `concepts` 是抽象知識或主題，盡量收斂，不要把完整句子塞進 concept。
4. concept 命名盡量使用以下風格：
   - 主題：XXX
   - 產業：XXX
   - 子產業：XXX
   - 技術：XXX
   - 地區：XXX
5. 不要重複，不要輸出空字串。
6. 盡量沿用原始資料中的公司名、技術名與主題名；必要時做語意收斂。
7. 不要發明原始資料沒有支持的結論。
8. 對每個 insight 都回傳 `entities` 和 `concepts`。
9. `source_entities` 與 `source_concepts` 是整份 source 的高階摘要，不要只是把所有 insight 全部複製一遍。
"""


def normalize_for_match(text: str) -> str:
    return f" {re.sub(r'\s+', ' ', text).strip().lower()} "


def infer_keyword_concepts(text: str) -> set[str]:
    normalized = normalize_for_match(text)
    concepts: set[str] = set()
    for concept, keywords in KEYWORD_CONCEPTS.items():
        if any(keyword.lower() in normalized for keyword in keywords):
            concepts.add(f"主題：{concept}")
    return concepts


def infer_keypoint_entities(pairs: dict[str, str]) -> set[str]:
    entities: set[str] = set()
    for key in ENTITY_PAIR_KEYS:
        value = pairs.get(key)
        if value:
            entities.add(str(value).strip())
    return {item for item in entities if item}


def infer_keypoint_concepts(pairs: dict[str, str]) -> set[str]:
    concepts: set[str] = set()
    for key, prefix in ALIAS_CONCEPT_PREFIXES.items():
        value = pairs.get(key)
        if value:
            concepts.add(f"{prefix}：{value}")
    return concepts


def infer_content_entities(text: str) -> set[str]:
    entities: set[str] = set()
    for token in TOKEN_RE.findall(text):
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned.isdigit():
            continue
        if cleaned.lower() in {"json", "csv", "api", "wiki", "markdown", "data", "stock"}:
            continue
        if batch_ingest.TICKER_RE.fullmatch(cleaned):
            entities.add(cleaned)
    return entities


def heuristic_enrich_payload(payload: dict[str, object]) -> dict[str, object]:
    enriched = copy.deepcopy(payload)
    source_level_concepts = set(batch_ingest.extract_named_items(enriched.get("concepts", [])))
    source_level_entities = set(batch_ingest.extract_named_items(enriched.get("entities", [])))
    podcast_name = str(enriched.get("podcast_name", "")).strip()
    if podcast_name:
        source_level_entities.add(batch_ingest.source_entity_name(podcast_name))

    new_insights = []
    for raw_insight in enriched.get("insights", []) or []:
        if not isinstance(raw_insight, dict):
            continue
        insight = dict(raw_insight)
        content = str(insight.get("content", ""))
        key_points = [str(point) for point in insight.get("key_points", []) or []]
        pairs = batch_ingest.parse_keypoint_pairs(key_points)
        combined_text = " ".join([content] + key_points)

        explicit_entities = batch_ingest.extract_named_items(insight.get("entities", []))
        explicit_concepts = batch_ingest.extract_named_items(insight.get("concepts", []))
        explicit_entities |= infer_keypoint_entities(pairs)
        explicit_entities |= infer_content_entities(combined_text)
        explicit_concepts |= infer_keypoint_concepts(pairs)
        explicit_concepts |= infer_keyword_concepts(combined_text)

        theme = pairs.get("theme")
        if theme:
            source_level_concepts.add(f"主題：{theme}")

        insight["entities"] = sorted(explicit_entities)
        insight["concepts"] = sorted(explicit_concepts)
        new_insights.append(insight)

    enriched["insights"] = new_insights
    enriched["entities"] = sorted(source_level_entities)
    enriched["concepts"] = sorted(source_level_concepts)
    return enriched


def build_agent_prompt(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "請閱讀以下 raw payload，並輸出 JSON。\n"
        "JSON 結構必須是：\n"
        "{\n"
        '  "source_entities": ["..."],\n'
        '  "source_concepts": ["..."],\n'
        '  "insights": [\n'
        "    {\n"
        '      "index": 0,\n'
        '      "summary": "一句摘要",\n'
        '      "entities": ["..."],\n'
        '      "concepts": ["..."]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "請注意 index 要對應原始 insights 的順序。\n\n"
        f"raw payload:\n{serialized}\n"
    )


def build_client(api_key: str | None = None) -> genai.Client:
    resolved_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not resolved_key:
        raise RuntimeError("找不到 GEMINI_API_KEY。請先設定 GEMINI_API_KEY 或 GOOGLE_API_KEY，再執行 wiki/agent_ingest.py")
    return genai.Client(api_key=resolved_key)


def parse_llm_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Gemini 沒有回傳內容")
    match = JSON_BLOCK_RE.search(raw)
    if match:
        raw = match.group(1)
    return json.loads(raw)


def call_gemini_for_payload(payload: dict[str, object], client: Any, model: str) -> dict[str, Any]:
    prompt = build_agent_prompt(payload)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    text = getattr(response, "text", None)
    if not text and hasattr(response, "parsed"):
        return dict(response.parsed)
    return parse_llm_json(text or "")


def merge_llm_output(base_payload: dict[str, object], llm_output: dict[str, Any]) -> dict[str, object]:
    merged = copy.deepcopy(base_payload)
    merged["entities"] = sorted(
        set(batch_ingest.extract_named_items(merged.get("entities", [])))
        | set(batch_ingest.extract_named_items(llm_output.get("source_entities", [])))
    )
    merged["concepts"] = sorted(
        set(batch_ingest.extract_named_items(merged.get("concepts", [])))
        | set(batch_ingest.extract_named_items(llm_output.get("source_concepts", [])))
    )

    insight_map: dict[int, dict[str, Any]] = {}
    for item in llm_output.get("insights", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        insight_map[index] = item

    new_insights = []
    for index, raw_insight in enumerate(merged.get("insights", []) or []):
        if not isinstance(raw_insight, dict):
            continue
        insight = dict(raw_insight)
        llm_item = insight_map.get(index, {})
        merged_entities = set(batch_ingest.extract_named_items(insight.get("entities", [])))
        merged_entities |= set(batch_ingest.extract_named_items(llm_item.get("entities", [])))
        merged_concepts = set(batch_ingest.extract_named_items(insight.get("concepts", [])))
        merged_concepts |= set(batch_ingest.extract_named_items(llm_item.get("concepts", [])))
        insight["entities"] = sorted(merged_entities)
        insight["concepts"] = sorted(merged_concepts)

        summary = str(llm_item.get("summary", "")).strip()
        if summary:
            key_points = [str(point) for point in insight.get("key_points", []) or []]
            llm_note = f"llm_summary: {summary}"
            if llm_note not in key_points:
                key_points.insert(0, llm_note)
            insight["key_points"] = key_points
        new_insights.append(insight)

    merged["insights"] = new_insights
    return merged


def enrich_payload_with_gemini(payload: dict[str, object], client: Any = None, model: str | None = None) -> dict[str, object]:
    heuristic_payload = heuristic_enrich_payload(payload)
    resolved_client = client or build_client()
    resolved_model = model or DEFAULT_GEMINI_MODEL
    llm_output = call_gemini_for_payload(payload, resolved_client, resolved_model)
    return merge_llm_output(heuristic_payload, llm_output)


def run_agent_ingest(root_dir: Path, client: Any = None, model: str | None = None) -> dict[str, int]:
    pending_files = batch_ingest.discover_pending_raw_files(root_dir)
    target_files = pending_files or sorted(
        path for path in (root_dir / "raw").glob("*.json") if path.name not in batch_ingest.SOURCE_SKIP
    )

    wiki_dir = root_dir / batch_ingest.WIKI_DIRNAME
    for subdir in ("concepts", "entities", "sources", "synthesis"):
        (wiki_dir / subdir).mkdir(parents=True, exist_ok=True)

    source_records: list[dict[str, object]] = []
    concept_agg: dict[str, dict[str, object]] = defaultdict(lambda: {"sources": set(), "notes": [], "related": set()})
    entity_agg: dict[str, dict[str, object]] = defaultdict(lambda: {"sources": set(), "notes": [], "related": set()})
    log_entries: list[str] = []

    for raw_path in target_files:
        payload = json.loads(raw_path.read_text())
        enriched_payload = enrich_payload_with_gemini(payload, client=client, model=model)
        source_record, concept_notes, entity_notes = batch_ingest.build_source_output(raw_path.name, enriched_payload)
        source_records.append(source_record)

        source_path = batch_ingest.get_page_path(root_dir, "source", str(source_record["title"]), raw_path.name)
        source_path.write_text(batch_ingest.render_source_page(source_record))

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

        concept_pages = sorted(source_record["concepts"])[:8]
        entity_pages = sorted(source_record["entities"])[:10]
        log_entries.append(
            "\n".join(
                [
                    f"## [{batch_ingest.TODAY}] agent_ingest | {source_record['title']}",
                    f"- 新增 sources/{source_path.name}",
                    f"- Gemini 補強概念頁：{ '、'.join(f'[[{name}]]' for name in concept_pages) if concept_pages else '無' }",
                    f"- Gemini 補強實體頁：{ '、'.join(f'[[{name}]]' for name in entity_pages) if entity_pages else '無' }",
                ]
            )
        )

    for title, data in concept_agg.items():
        path = batch_ingest.get_page_path(root_dir, "concept", title)
        intro = "\n\n".join([f"# {title}", "", batch_ingest.infer_concept_summary(title, data["notes"])])
        related = sorted(set(data["related"]) | {"整體知識摘要"})
        batch_ingest.write_page(
            path=path,
            page_type="concept",
            title=title,
            tags=["概念", "agent", "gemini"],
            sources=sorted(data["sources"]),
            manual_intro=intro,
            notes=data["notes"],
            related_titles=related,
        )

    for title, data in entity_agg.items():
        path = batch_ingest.get_page_path(root_dir, "entity", title)
        tags = batch_ingest.merge_lists(batch_ingest.infer_entity_tags(title, data["notes"]), ["agent", "gemini"])
        intro = "\n\n".join([f"# {title}", "", batch_ingest.infer_entity_summary(title, data["notes"])])
        related = sorted(set(data["related"]) | {"整體知識摘要"})
        batch_ingest.write_page(
            path=path,
            page_type="entity",
            title=title,
            tags=tags,
            sources=sorted(data["sources"]),
            manual_intro=intro,
            notes=data["notes"],
            related_titles=related,
        )

    log_entries.extend(batch_ingest.create_synthesis_pages(root_dir, source_records))
    batch_ingest.rebuild_overview(root_dir)
    batch_ingest.rebuild_index(root_dir)
    batch_ingest.append_log_entries(root_dir, log_entries)

    return {
        "processed": len(target_files),
        "sources": len(source_records),
        "concepts": len(concept_agg),
        "entities": len(entity_agg),
    }


def main() -> None:
    result = run_agent_ingest(Path(__file__).parent.parent)
    print(f"processed {result['processed']} raw files")
    print(f"agent_created_or_updated_sources {result['sources']}")
    print(f"agent_created_or_updated_concepts {result['concepts']}")
    print(f"agent_created_or_updated_entities {result['entities']}")


if __name__ == "__main__":
    main()
