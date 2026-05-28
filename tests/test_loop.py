import json
from pathlib import Path

from agent.budget import BudgetConfig, BudgetSentinel
from agent.loop import AgentLoop
from agent.model_router import ModelRouter
from observability.state_store import StateStore
from shared.schemas import MerchantConfig
from tools.registry import ToolRegistry


def test_loop_completes_simple_merchant():
    registry = ToolRegistry()
    registry.load_all()
    rows = json.loads(Path("data/mock_db.json").read_text(encoding="utf-8"))
    mock_db = {}
    for row in rows:
        mock_db.setdefault(row["merchant"], []).append(row)
    scenario = {
        "merchant_fixtures": {
            "Amazon": {
                "extracted_deals": mock_db["Amazon"],
                "days_since_last_update": 2,
            }
        }
    }
    loop = AgentLoop(registry, BudgetSentinel(BudgetConfig()), StateStore(), ModelRouter(), mock_db, scenario=scenario)
    report = loop.run(
        [
            MerchantConfig(
                name="Amazon",
                deals_url="https://example.com/amazon",
                category="Electronics",
                js_rendered=False,
                known_blocks=[],
            )
        ]
    )
    assert report.merchant_results[0].status == "DONE"
