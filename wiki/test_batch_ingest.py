import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import batch_ingest  # noqa: E402


class BatchIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "synthesis").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_sample_raw(self) -> Path:
        payload = {
            "episode": "AI 與半導體產業觀察樣本",
            "podcast_name": "yfinance",
            "date": "2026-04-08",
            "insights": [
                {
                    "type": "stock",
                    "content": "NVIDIA 屬於 Technology / Semiconductors，是 AI 供應鏈的重要樣本。",
                    "tickers": ["NVDA"],
                    "key_points": [
                        "company: NVIDIA",
                        "sector: Technology",
                        "industry: Semiconductors",
                    ],
                }
            ],
        }
        path = self.root / "raw" / "2026-04-08_ai_sample.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def test_run_ingest_creates_source_concept_entity_and_indexes(self) -> None:
        self.write_sample_raw()

        result = batch_ingest.run_ingest(self.root)

        self.assertEqual(result["processed"], 1)
        self.assertTrue((self.root / "wiki" / "sources" / "2026-04-08_ai_sample.md").exists())
        self.assertTrue((self.root / "wiki" / "entities" / "NVIDIA.md").exists())
        self.assertTrue((self.root / "wiki" / "entities" / "NVDA.md").exists())
        self.assertTrue((self.root / "wiki" / "concepts" / "個股觀察.md").exists())
        self.assertTrue((self.root / "wiki" / "concepts" / "產業：Technology.md").exists())

        index_text = (self.root / "wiki" / "index.md").read_text()
        log_text = (self.root / "wiki" / "log.md").read_text()
        overview_text = (self.root / "wiki" / "overview.md").read_text()

        self.assertIn("[[AI 與半導體產業觀察樣本]]", index_text)
        self.assertIn("ingest | AI 與半導體產業觀察樣本", log_text)
        self.assertIn("本 wiki 目前完成 1 份來源摘要", overview_text)

    def test_run_ingest_preserves_manual_body_when_updating_existing_page(self) -> None:
        self.write_sample_raw()
        concept_path = self.root / "wiki" / "concepts" / "個股觀察.md"
        concept_path.write_text(
            "\n".join(
                [
                    "---",
                    "title: 個股觀察",
                    "type: concept",
                    "tags: [概念]",
                    "created: 2026-04-01",
                    "updated: 2026-04-01",
                    "sources: []",
                    "---",
                    "# 個股觀察",
                    "",
                    "這是手動寫的摘要。",
                    "",
                    "## 相關頁面",
                    "- [[整體知識摘要]]",
                ]
            )
            + "\n"
        )

        batch_ingest.run_ingest(self.root)

        text = concept_path.read_text()
        self.assertIn("這是手動寫的摘要。", text)
        self.assertIn("2026-04-08_ai_sample.json", text)
        self.assertIn("<!-- AUTO-INGEST START -->", text)


if __name__ == "__main__":
    unittest.main()
