"""Agent 调度中心（铁路购票）：自然语言 -> Java 接口参数。

流程：每轮先做一次 RAG 检索，把【参考资料】注入上下文；再由模型以 function-calling
循环决策——直接依据资料作答，或调用工具（查票/下单/支付/退票等）。

改数据的动作不交给模型：buy_ticket 的 tripId 必须来自上一轮查票结果，下单走两阶段确认。
"""
import logging
import uuid
from datetime import date
from typing import AsyncIterator, Optional

from .. import config, java_client, llm, tools
from ..llm import LlmError
from ..middleware import current_user
from ..rag.store import RagStore, get_store, rag_available
from . import history as hist
from . import memory as mem

logger = logging.getLogger(__name__)

# 动作词：命中则视为"办业务"而非"问规则"，避免规则词把下单请求吞进 RAG
ACTION_WORDS = ("买", "订", "购", "预约", "帮我查", "下单")

CONFIRM_WORDS = ("确认", "是的", "可以", "好的", "就买", "下单", "买吧")


def _event(kind: str, text: str, task: str = "agent") -> dict:
    return {"kind": kind, "text": text, "model": config.resolve_model(task)}


def _find_trip(rows, trip_id):
    """从上一轮查票结果里取出 tripId 对应的行；取不到返回 None。

    这是"防模型臆造车次"的落点：模型给出的 tripId 必须能在查票结果里找到，
    否则拒绝下单。历史上此逻辑在生产代码里是内联写的，这里抽出来以便单测覆盖。
    """
    if not rows:
        return None
    return next((r for r in rows if r.get("tripId") == trip_id), None)


def _system_prompt() -> str:
    return (
        "你是铁路购票助手。今天是%s。\n"
        "处理顺序（务必遵守）：\n"
        "1. 规则/规定/知识类问题（退改签/儿童/学生/票价/携带行李等）先依据【参考资料】回答，资料覆盖就不要再调工具；资料未覆盖则明说“未查到相关规定”。\n"
        "2. 业务操作（查票/购票/我的订单/订单详情/支付/退票/常用联系人/个人资料/列车车次车站列表等）调用工具走 Java 完成；退票等操作若不确定是哪张，先调 my_orders 列出再让用户确认 requestId。\n"
        "3. 参考资料与可用工具都无法满足用户请求时，明确回答“我暂时无法办理/不清楚，请补充信息或联系人工”，绝不编造结论、车次或参数。\n"
        "必要参数缺失（如出发地/日期）时直接问用户缺什么。金额单位是分。说话简洁公文化。"
        % date.today().isoformat()
    )


class AgentService:
    def __init__(self) -> None:
        self._rag: Optional[RagStore] = None
        # 最近一次查票结果（按用户），用于约束 buy_ticket 的 tripId 必须来自查询
        self._rows: dict[int, list] = {}
        # 待确认的下单意向（按用户）：模型反向复述后，用户确认才真正下单
        self._intents: dict[int, dict] = {}
        # 刚下单的待支付订单 requestId（按用户），便于用户回“支付”完成付款
        self._orders: dict[int, str] = {}

    def _rag_store(self) -> Optional[RagStore]:
        if self._rag is None:
            self._rag = get_store() if rag_available() else None
        return self._rag

    async def handle(self, user_text: str) -> AsyncIterator[dict]:
        user = current_user()
        yield _event("think", f"已收到请求，正在为您办理（{user.identity}）。")

        try:
            stations = await java_client.stations()
        except Exception as e:  # noqa: BLE001
            logger.exception("车站服务不可用")
            stations = []
        station_line = "、".join(s["name"] for s in stations[:30])

        # 两段式确认：上一轮已"反向复述待确认"的意向/待支付订单，本轮回“确认/支付”即执行
        pay_rid = self._orders.get(user.user_id)
        if pay_rid and any(w in user_text for w in ("支付", "付款", "付钱")):
            self._orders.pop(user.user_id, None)
            try:
                r = await java_client.pay(pay_rid)
                yield _event("result", f"订单 {pay_rid} 已支付，状态 {r.get('status')}。")
            except java_client.JavaError as e:
                yield _event("error", e.msg)
            return
        intent = self._intents.get(user.user_id)
        if intent and any(w in user_text for w in CONFIRM_WORDS):
            # 用户确认：执行预填的下单
            self._intents.pop(user.user_id, None)
            async for ev in self._commit_buy(user, intent):
                yield ev
            return
        if intent:
            # 用户改了主意/新请求：作废旧意向，落到下面按新请求处理
            self._intents.pop(user.user_id, None)

        # 记忆按会话隔离：上下文取自“当前会话”，切会话即切上下文
        session = hist.current_session(user.user_id)
        state = mem.load_state(user.user_id, session=session)

        # 每轮先做一次 RAG 检索：把相关规则/知识作为参考资料注入，模型先看资料再决定要不要调 Java 工具
        refs: list[str] = []
        rag = self._rag_store()
        if rag and rag.enabled:
            try:
                refs = await rag.retrieve(user_text, department=user.department, top_k=4)
            except Exception as e:  # noqa: BLE001
                logger.warning("RAG 检索失败，跳过参考注入：%s", e)
        ref_block = "【参考资料】\n" + "\n---\n".join(r.strip() for r in refs if r and r.strip()) \
            if refs else "【参考资料】（未检索到相关资料）"

        sys_content = _system_prompt()
        if state["summary"]:
            # 早期上下文已压缩成摘要，注入 system，避免无限增长
            sys_content += f"\n\n【此前对话摘要】{state['summary']}"
        msgs = [{"role": "system", "content": sys_content}]
        msgs.extend(state["msgs"])  # 近期原文（最近几轮，保证指代细节）
        msgs.append({"role": "user",
                     "content": f"已知车站：{station_line}\n\n{ref_block}\n\n用户请求：{user_text}"})

        rounds = 0
        shown = False
        while rounds < 4:
            rounds += 1
            try:
                out = await llm.chat(task="agent", messages=msgs, tools=tools.all_schemas())
            except LlmError as e:
                yield _event("error", str(e))
                return

            if not out.tool_calls:
                # 模型未调工具：把它基于参考资料给出的答复照实给用户；
                # 空回复则区分“疑似办业务”与“问不到答不出”，给补充引导或诚实说明无法办理
                if out.content and out.content.strip():
                    yield _event("answer", out.content)
                elif shown:
                    pass  # 结果事件已给出，无需再补空话
                elif any(a in user_text for a in ACTION_WORDS):
                    yield _event("answer", "请补充需要办理的内容（如出发地/到达地/日期/席别），或说明要操作哪张订单。")
                else:
                    yield _event("answer", "抱歉，我暂时无法办理或未查到相关资料。请补充说明，或联系人工处理。")
                return

            acted = False
            for tc in out.tool_calls:
                name, args = tc.name, tc.arg_dict()
                try:
                    if name == "query_tickets":
                        rows = await java_client.query_tickets(
                            args.get("from", ""), args.get("to", ""),
                            args.get("date", date.today().isoformat()),
                            args.get("seatClass"))
                        self._rows[user.user_id] = rows
                        shown = True
                        acted = True
                        text = self._query_text(rows)
                        msgs.append({"role": "assistant", "content": f"查票结果：\n{text}"})
                        yield _event("result", text)
                    elif name == "buy_ticket":
                        # 防臆造 + 反向复述：tripId 必须来自查票结果，且下单前请用户确认
                        row = _find_trip(self._rows.get(user.user_id, []), args.get("tripId"))
                        if row is None:
                            yield _event("error", "请先使用 query_tickets 查询车次，再从结果中选择要购买的车次。")
                            return
                        intent = {
                            "tripId": row["tripId"],
                            "seatClass": row["seatClass"],
                            "from": row["from"],
                            "to": row["to"],
                            "date": row.get("travelDate"),
                            "trainCode": row.get("trainCode"),
                            "ticketType": (args.get("ticketType") or "ADULT").upper(),
                        }
                        self._intents[user.user_id] = intent
                        shown = True
                        acted = True
                        base = (f"请确认下单：{intent['trainCode']} {intent['date']} "
                                f"{intent['from']}→{intent['to']} {intent['seatClass']} "
                                f"({intent['ticketType']}) 回复“确认”即出票。")
                        if intent["ticketType"] in ("CHILD", "STUDENT"):
                            rule = await self._rule_snippet(
                                "儿童票" if intent["ticketType"] == "CHILD" else "学生票")
                            if rule:
                                base = f"下单前请核对规则：{rule[:180]}\n{base}"
                        confirm = base
                        msgs.append({"role": "assistant", "content": confirm})
                        yield _event("confirm", confirm)
                        return
                    elif name == "my_orders":
                        rows = await java_client.my_orders()
                        shown = True
                        acted = True
                        text = self._orders_text(rows)
                        msgs.append({"role": "assistant", "content": f"我的订单：\n{text}"})
                        yield _event("result", text)
                    elif name == "order_detail":
                        d = await java_client.order_detail(args["requestId"])
                        shown = True
                        acted = True
                        text = self._kv_text(d)
                        msgs.append({"role": "assistant", "content": f"订单详情：\n{text}"})
                        yield _event("result", text)
                    elif name in ("list_stations", "list_trains", "list_trips", "list_contacts"):
                        if name == "list_stations":
                            rows = await java_client.stations(args.get("kw") or "")
                        elif name == "list_trains":
                            rows = await java_client.list_trains()
                        elif name == "list_trips":
                            rows = await java_client.list_trips(args.get("date"), args.get("trainId"))
                        else:
                            rows = await java_client.list_contacts()
                        shown = True
                        acted = True
                        text = self._kv_text(rows)
                        msgs.append({"role": "assistant", "content": f"{name}：\n{text}"})
                        yield _event("result", text)
                    elif name == "add_contact":
                        c = await java_client.add_contact(
                            args["name"], args.get("idType"), args.get("idNo"),
                            args.get("phone"), args.get("age"))
                        shown = True
                        acted = True
                        line = f"已新增联系人：{c.get('name')}（id={c.get('id')}）"
                        msgs.append({"role": "assistant", "content": line})
                        yield _event("result", line)
                    elif name == "get_profile":
                        p = await java_client.get_profile()
                        shown = True
                        acted = True
                        text = self._kv_text(p)
                        msgs.append({"role": "assistant", "content": f"个人资料：\n{text}"})
                        yield _event("result", text)
                    elif name == "pay_ticket":
                        rid = args["requestId"]
                        r = await java_client.pay(rid)
                        shown = True
                        acted = True
                        line = f"订单 {rid} 已支付，状态 {r.get('status')}"
                        msgs.append({"role": "assistant", "content": line})
                        yield _event("result", line)
                    elif name == "cancel_ticket":
                        rid = args["requestId"]
                        r = await java_client.cancel(rid)
                        shown = True
                        acted = True
                        line = f"订单 {rid} 已退票，状态 {r.get('status')}，余票已回补"
                        msgs.append({"role": "assistant", "content": line})
                        yield _event("result", line)
                except java_client.JavaError as e:
                    yield _event("error", f"{e.msg}")
                    return

            if not acted:
                break

            # 是否还需要继续（如查完票再下单）：再问一次模型，让它决定收尾还是下单
            msgs.append({"role": "user",
                         "content": "基于以上结果：如果还需继续就调用下一个工具；已完事则仅用一句话回复用户。"})
        # 循环结束兜底：结果已由各 [result] 给出；完全没出结果则诚实说明，不编造
        if not shown:
            yield _event("answer", "抱歉，暂时没有可用工具或资料能办理这件事。请换一种说法，或补充车次/日期/席别/订单号。")

    async def _commit_buy(self, user, intent) -> AsyncIterator[dict]:
        """用户确认后执行预填下单（确定性路径，不再让模型介入）。"""
        try:
            r = await java_client.buy_ticket(
                int(intent["tripId"]), intent["seatClass"], intent["from"], intent["to"],
                ticket_type=intent.get("ticketType", "ADULT"))
        except java_client.JavaError as e:
            yield _event("error", e.msg)
            return
        rid = r.get("requestId")
        if rid:
            self._orders[user.user_id] = rid
        line = (f"下单成功：{r.get('from')}->{r.get('to')} {r.get('seatClass')}，"
                f"金额 {r.get('priceCents')}分，状态 {r.get('status')}，订单号 {r.get('orderNo')}。"
                f"回复“支付”完成付款。")
        yield _event("result", line)

    async def _rule_snippet(self, keyword: str) -> str:
        """RAG 取一条规则摘要，供下单确认前置（A 层：信息前置）。"""
        rag = self._rag_store()
        if rag and rag.enabled:
            try:
                hits = await rag.retrieve(keyword, department=current_user().department, top_k=1)
                if hits:
                    return hits[0].strip()
            except Exception:  # noqa: BLE001
                pass
        return ""

    @staticmethod
    def _query_text(rows: list[dict]) -> str:
        if not rows:
            return "该区间当日无可用车次或余票。"
        lines = []
        for r in rows:
            lines.append(f"- tripId={r['tripId']} {r['trainCode']} {r['from']}->{r['to']} "
                         f"{r['departTime']}-{r['arriveTime']} {r['seatClass']} "
                         f"{r['priceCents']}分 余{r['remaining']}张")
        return "\n".join(lines)

    @staticmethod
    def _orders_text(rows: list[dict]) -> str:
        if not rows:
            return "当前没有订单。"
        return "\n".join(
            f"- {o.get('orderNo')} {o.get('from')}->{o.get('to')} {o.get('seatClass')} "
            f"{o.get('priceCents')}分 {o.get('status')} requestId={o.get('requestId')}"
            for o in rows)

    @staticmethod
    def _kv_text(obj) -> str:
        """把单个对象或对象数组渲染成可读文本（供订单详情/车站/列车/车次/联系人/资料等通用展示）。"""
        def line(r: dict) -> str:
            return ", ".join(f"{k}={v}" for k, v in r.items() if v is not None)
        if isinstance(obj, list):
            if not obj:
                return "暂无数据。"
            return "\n".join(f"- {line(r)}" for r in obj)
        if not obj:
            return "暂无数据。"
        return line(obj)
