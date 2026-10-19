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


# ── 确认判定 ──────────────────────────────────────────────────────────

INTENT = {"intent_id": "abc123", "tripId": 2}


def test_confirm_requires_matching_intent_id(monkeypatch):
    """结构化确认：intent_id 必须与挂起的那一单对得上。"""
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    d = orchestrator._intent_decision

    assert d(INTENT, "", {"confirm": True, "intent_id": "abc123"}) is True
    # intent_id 不匹配 → 拒绝（串话 / 重放）
    assert d(INTENT, "", {"confirm": True, "intent_id": "WRONG"}) is False
    # 带了 intent_id 但明确取消
    assert d(INTENT, "", {"confirm": False, "intent_id": "abc123"}) is False


def test_legacy_keyword_fallback_is_switchable(monkeypatch):
    """过渡期开关：默认开（不破坏现有前端），置 0 后打字不再触发出票。"""
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "1")
    assert orchestrator._intent_decision(INTENT, "好的", None) is True

    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    assert orchestrator._intent_decision(INTENT, "好的", None) is False
    assert orchestrator._intent_decision(INTENT, "确认", None) is False


def test_structured_path_unaffected_by_legacy_switch(monkeypatch):
    """关掉回退之后，结构化路径照常工作 —— 这是最终形态。"""
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    assert orchestrator._intent_decision(
        INTENT, "随便说点什么", {"confirm": True, "intent_id": "abc123"}) is True


def test_legacy_switch_never_hijacks_an_explicit_cancel(monkeypatch):
    """★ 本次修掉的核心缺陷。

    用户说「好的，但我想改时间」—— 旧实现因为句中有「好的」而判定为确认并下单。
    新实现里前端没点确认按钮就不发 confirm 载荷；即便回退开关还开着，
    用户说这句话时前端也不会发 confirm，所以不会出票。
    这里断言的是：该句子在旧规则下确实会被误判（缺陷真实存在），
    而结构化路径给出的是 False。
    """
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    bad = "好的，但我想改时间"

    # 缺陷本身：旧的关键词规则会命中
    assert any(w in bad for w in orchestrator.LEGACY_CONFIRM_WORDS) is True

    # 新路径：没有结构化载荷 → 不下单
    assert orchestrator._intent_decision(INTENT, bad, None) is False
    # 前端点了「取消」→ 也不下单
    assert orchestrator._intent_decision(
        INTENT, bad, {"confirm": False, "intent_id": "abc123"}) is False


def test_pay_decision_mirrors_intent_decision(monkeypatch):
    """支付确认与下单确认走同一套策略。"""
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "1")
    assert orchestrator._pay_decision("支付", None) is True
    assert orchestrator._pay_decision("随便说说", None) is False

    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    assert orchestrator._pay_decision("支付", None) is False
    # 支付载荷必须显式声明 action=pay
    assert orchestrator._pay_decision("", {"action": "pay", "confirm": True}) is True


def test_pay_requires_explicit_action_scope(monkeypatch):
    """★ 端到端实测发现的缺陷。

    支付是不可逆动作。曾经的写法是「只要 resume 里 confirm 为真就付」——
    结果一个**过期的下单确认**（带 confirm=true 但 intent_id 对不上）
    会把之前那笔待支付订单顺带付掉。

    真机复现：场景 2 发 {"confirm": true, "intent_id": "deadbeef0000"}，
    返回的却是「订单 ... 已支付，状态 PAID」。
    """
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")

    # 下单确认载荷不能触发支付
    assert orchestrator._pay_decision("", {"confirm": True, "intent_id": "abc"}) is False
    # 过期/串话的下单载荷同样不能
    assert orchestrator._pay_decision("", {"confirm": True, "intent_id": "deadbeef"}) is False
    # 取消载荷也不能
    assert orchestrator._pay_decision("", {"action": "buy", "confirm": False}) is False
    # 只有显式的支付载荷可以
    assert orchestrator._pay_decision("", {"action": "pay", "confirm": True}) is True
    assert orchestrator._pay_decision("", {"action": "pay", "confirm": False}) is False


def test_intent_decision_ignores_non_buy_payload(monkeypatch):
    """反向：支付载荷不该被当成下单确认。"""
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    assert orchestrator._intent_decision(
        INTENT, "", {"action": "pay", "confirm": True}) is False


def test_confirm_event_carries_intent_id():
    """确认事件必须带 intent_id —— 前端要靠它回传，这是结构化确认的前提。"""
    ev = {**orchestrator._event("confirm", "请确认下单"), "intent_id": "abc123"}
    assert ev["kind"] == "confirm" and ev["intent_id"] == "abc123"


# ── 意向处置（_intent_verdict）────────────────────────────────────────

def test_intent_verdict_matrix(monkeypatch):
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    v = orchestrator._intent_verdict

    # 载荷对得上：确认执行、取消作废
    assert v(INTENT, "", {"confirm": True, "intent_id": "abc123"}) == "execute"
    assert v(INTENT, "", {"confirm": False, "intent_id": "abc123"}) == "cancel"
    # 纯文本且未命中确认词：用户改了主意
    assert v(INTENT, "帮我查别的", None) == "discard"
    # 过渡期关键词仍然生效
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "1")
    assert v(INTENT, "好的", None) == "execute"


def test_unrelated_payload_does_not_discard_pending_intent(monkeypatch):
    """★ 本次修掉的缺陷。

    原实现：intent 存在但判定未命中时**无条件** drop。于是一次无关载荷
    （比如没有待支付单时误发的支付载荷、或一个过期/串话的载荷）会把用户刚看到的
    「确认下单」按钮悄悄废掉 —— 用户再点就没反应，且不知道发生了什么。

    现在只有「确实是冲这次意向来的」（intent_id 对得上）或「用户用自然语言改了主意」
    才作废；对不上的载荷一律 keep。
    """
    monkeypatch.setenv("AGENT_CONFIRM_LEGACY", "0")
    v = orchestrator._intent_verdict

    # 别的用途的载荷（支付）
    assert v(INTENT, "", {"action": "pay", "confirm": True}) == "keep"
    # 过期/串话的 intent_id
    assert v(INTENT, "", {"confirm": True, "intent_id": "deadbeef"}) == "keep"
    assert v(INTENT, "", {"confirm": False, "intent_id": "deadbeef"}) == "keep"
    # 空载荷也算对不上
    assert v(INTENT, "", {}) == "keep"


# ── 写交易的 requestId 只能来自系统记下的订单（_known_order_ids）──────

def test_known_order_ids_covers_just_created_and_listed(monkeypatch):
    """★ 本次补上的防线。

    原来的实现把模型传的 requestId 直接交给 Java。Java 侧有归属校验（付不了别人的单），
    但模型仍可以在用户没点确认的情况下，把该用户自己的订单付掉/退掉 ——
    「写数据由确定性代码执行」就漏在这儿。
    """
    store = {"order": "REQ-NEW", "orders": [{"requestId": "REQ-B"}, {"requestId": "REQ-C"}]}
    monkeypatch.setattr(orchestrator.pending, "get", lambda uid, field: store.get(field))

    ids = orchestrator._known_order_ids(1)
    assert ids == {"REQ-NEW", "REQ-B", "REQ-C"}
    # 模型编造的 id 不在集合里 —— 工具分发处会据此拒绝
    assert "REQ-INVENTED" not in ids


def test_known_order_ids_empty_when_nothing_seen(monkeypatch):
    """什么都没见过时集合为空：模型任何 requestId 都会被拒，必须先 my_orders。"""
    monkeypatch.setattr(orchestrator.pending, "get", lambda uid, field: None)
    assert orchestrator._known_order_ids(1) == set()


def test_known_order_ids_tolerates_rows_without_request_id(monkeypatch):
    store = {"order": None, "orders": [{"orderNo": "T1"}, {"requestId": "REQ-X"}]}
    monkeypatch.setattr(orchestrator.pending, "get", lambda uid, field: store.get(field))
    assert orchestrator._known_order_ids(1) == {"REQ-X"}


# ── 工具必填参数 ──────────────────────────────────────────────────────

def test_req_arg_returns_trimmed_value():
    assert orchestrator._req_arg({"requestId": "  REQ-1 "}, "requestId") == "REQ-1"
    assert orchestrator._req_arg({"age": 8}, "age") == "8"


def test_req_arg_raises_tool_arg_error_instead_of_key_error():
    """★ 实测暴露的缺陷。

    原先直接写 `args["requestId"]`：模型漏传就抛 KeyError，它不是 JavaError，
    不会被分发处的 except 接住，一路冒到 chat.py 的兜底 except，
    用户只看到「处理出错：'requestId'」。现在统一抛 ToolArgError，由分发处
    转成"某某调用不完整，请补充信息后重试"。
    """
    for bad in ({}, {"requestId": None}, {"requestId": ""}, {"requestId": "   "}):
        try:
            orchestrator._req_arg(bad, "requestId")
        except orchestrator.ToolArgError as e:
            assert "requestId" in str(e)
        else:
            raise AssertionError(f"应当抛 ToolArgError：{bad!r}")
    # 不能是 KeyError（那正是要被消掉的症状）
    assert not issubclass(orchestrator.ToolArgError, KeyError)
