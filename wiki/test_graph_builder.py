import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import batch_ingest  # noqa: E402
import graph_builder  # noqa: E402


class GraphBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki" / "synthesis").mkdir(parents=True, exist_ok=True)
        payload = {
            "episode": "網路安全產業樣本",
            "podcast_name": "yfinance",
            "date": "2026-04-08",
            "insights": [
                {
                    "type": "stock",
                    "content": "CrowdStrike 是網路安全產業的重要公司。",
                    "tickers": ["CRWD"],
                    "key_points": [
                        "company: CrowdStrike",
                        "sector: Technology",
                        "industry: Software - Infrastructure",
                    ],
                }
            ],
        }
        (self.root / "raw" / "2026-04-08_cyber_sample.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2)
        )
        batch_ingest.run_ingest(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_collect_graph_includes_raw_source_and_knowledge_nodes(self) -> None:
        graph = graph_builder.collect_graph(self.root / "wiki")
        nodes = {node["title"]: node for node in graph["nodes"]}
        links = {(link["source"], link["target"]) for link in graph["links"]}

        self.assertIn("網路安全產業樣本", nodes)
        self.assertIn("個股觀察", nodes)
        self.assertIn("CrowdStrike", nodes)
        self.assertIn("raw/2026-04-08_cyber_sample.json", nodes)
        self.assertIn(("raw/2026-04-08_cyber_sample.json", "網路安全產業樣本"), links)
        self.assertIn(("網路安全產業樣本", "個股觀察"), links)

    def test_render_html_embeds_general_controls(self) -> None:
        graph = graph_builder.collect_graph(self.root / "wiki")
        html = graph_builder.render_html(graph)

        self.assertIn("LLM Wiki General Graph", html)
        self.assertIn("顯示 raw 節點", html)
        self.assertIn("重新排版", html)
        self.assertIn("summary", html)


if __name__ == "__main__":
    unittest.main()
