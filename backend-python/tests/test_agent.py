"""Agent 关键纯逻辑单测：工具契约、结果格式化、防臆造车次、模型路由。

用法（venv 内）：python -m pytest tests -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import orchestrator  # noqa: E402
from app.tools import all_schemas  # noqa: E402


def test_tool_contract_has_core_business_tools():
    names = {t["function"]["name"] for t in all_schemas()}
    core = {"query_tickets", "buy_ticket", "my_orders", "pay_ticket", "cancel_ticket"}
    assert core <= names, names
    # 业务全量 + 只读：订单详情/车站/列车/车次/联系人/资料均需注册
    assert {"order_detail", "list_stations", "list_trains", "list_trips",
            "list_contacts", "add_contact", "get_profile"} <= names, names


def test_query_text_format_contains_key_fields():
    rows = [{"tripId": 2, "trainCode": "G101", "from": "北京南", "to": "上海虹桥",
             "departTime": "07:00", "arriveTime": "11:30", "seatClass": "二等座",
             "priceCents": 55300, "remaining": 600}]
    text = orchestrator.AgentService._query_text(rows)
    assert "G101" in text and "55300分" in text and "600张" in text


def test_resolve_model_defaults_to_official_id():
    from app import config
    # 未设置时别名应落到官方带连字符 id；已设置 MODEL_ID_* 也应为 deepseek-v4-pro
    assert config.MODEL_IDS.get("deepseekv4pro") == "deepseek-v4-pro"


def test_buy_trip_must_come_from_query_results():
    """防臆造车次：模型给出的 tripId 必须能在上一轮查票结果里找到，否则拒绝下单。"""
    rows = [{"tripId": 2, "trainCode": "G101"}]
    assert orchestrator._find_trip(rows, 2)["trainCode"] == "G101"
    assert orchestrator._find_trip(rows, 99) is None
    assert orchestrator._find_trip(None, 2) is None
    assert orchestrator._find_trip([], 2) is None
