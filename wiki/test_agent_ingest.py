import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import agent_ingest  # noqa: E402


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_content(self, **_: object) -> FakeResponse:
        return FakeResponse(self._text)


class FakeClient:
    def __init__(self, text: str) -> None:
        self.models = FakeModels(text)


class AgentIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        for subdir in ("concepts", "entities", "sources", "synthesis"):
            (self.root / "wiki" / subdir).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_enrich_payload_with_gemini_merges_llm_output(self) -> None:
        payload = {
            "episode": "量子運算與光通訊樣本",
            "podcast_name": "custom_research",
            "date": "2026-04-08",
            "insights": [
                {
                    "type": "stock",
                    "content": "IONQ 與 Rigetti 被視為量子電腦代表，CPO 與矽光子也可能帶動光通訊需求。",
                    "tickers": ["IONQ", "RGTI"],
                    "key_points": [
                        "company: IonQ",
                        "theme: 量子電腦",
                        "technology: CPO",
                    ],
                }
            ],
        }
        fake_json = json.dumps(
            {
                "source_entities": ["IonQ", "Rigetti"],
                "source_concepts": ["主題：量子電腦", "主題：光通訊"],
                "insights": [
                    {
                        "index": 0,
                        "summary": "量子電腦與光通訊主題在同一段資料中同時出現。",
                        "entities": ["IonQ", "RGTI"],
                        "concepts": ["技術：CPO", "主題：量子電腦", "主題：光通訊"],
                    }
                ],
            },
            ensure_ascii=False,
        )

        enriched = agent_ingest.enrich_payload_with_gemini(payload, client=FakeClient(fake_json), model="gemini-test")

        self.assertIn("主題：量子電腦", enriched["concepts"])
        self.assertIn("主題：光通訊", enriched["concepts"])
        self.assertIn("IonQ", enriched["entities"])
        self.assertIn("技術：CPO", enriched["insights"][0]["concepts"])
        self.assertTrue(any(point.startswith("llm_summary: ") for point in enriched["insights"][0]["key_points"]))

    def test_agent_ingest_writes_llm_backed_pages(self) -> None:
        payload = {
            "episode": "量子運算與光通訊樣本",
            "podcast_name": "custom_research",
            "date": "2026-04-08",
            "insights": [
                {
                    "type": "stock",
                    "content": "IONQ 與 Rigetti 被視為量子電腦代表，CPO 與矽光子也可能帶動光通訊需求。",
                    "tickers": ["IONQ", "RGTI"],
                    "key_points": ["company: IonQ", "theme: 量子電腦", "technology: CPO"],
                }
            ],
        }
        (self.root / "raw" / "2026-04-08_quantum.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        fake_json = json.dumps(
            {
                "source_entities": ["IonQ", "Rigetti"],
                "source_concepts": ["主題：量子電腦", "主題：光通訊"],
                "insights": [
                    {
                        "index": 0,
                        "summary": "量子電腦與光通訊主題在同一段資料中同時出現。",
                        "entities": ["IonQ", "Rigetti", "RGTI"],
                        "concepts": ["技術：CPO", "主題：量子電腦", "主題：光通訊"],
                    }
                ],
            },
            ensure_ascii=False,
        )

        result = agent_ingest.run_agent_ingest(self.root, client=FakeClient(fake_json), model="gemini-test")

        self.assertEqual(result["processed"], 1)
        self.assertTrue((self.root / "wiki" / "concepts" / "主題：量子電腦.md").exists())
        self.assertTrue((self.root / "wiki" / "concepts" / "技術：CPO.md").exists())
        self.assertTrue((self.root / "wiki" / "concepts" / "主題：光通訊.md").exists())
        self.assertTrue((self.root / "wiki" / "entities" / "IonQ.md").exists())
        self.assertTrue((self.root / "wiki" / "entities" / "Rigetti.md").exists())

        log_text = (self.root / "wiki" / "log.md").read_text()
        self.assertIn("agent_ingest | 量子運算與光通訊樣本", log_text)
        self.assertIn("Gemini 補強概念頁", log_text)


if __name__ == "__main__":
    unittest.main()
