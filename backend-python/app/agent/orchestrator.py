"""Agent 调度中心（铁路购票）：自然语言 -> Java 接口参数。

流程：每轮先做一次 RAG 检索，把【参考资料】注入上下文；再由模型以 function-calling
循环决策——直接依据资料作答，或调用工具（查票/下单/支付/退票等）。

改数据的动作不交给模型：buy_ticket 的 tripId 必须来自上一轮查票结果，下单走两阶段确认。
"""
import asyncio
import logging
import os
import uuid
from datetime import date
from typing import AsyncIterator, Optional

from .. import config, java_client, llm, tools
from ..llm import LlmError
from ..middleware import current_user
from ..rag.store import RagStore, get_store, rag_available
from . import history as hist
from . import memory as mem
from . import pending

logger = logging.getLogger(__name__)

# 动作词：命中则视为"办业务"而非"问规则"，避免规则词把下单请求吞进 RAG
ACTION_WORDS = ("买", "订", "购", "预约", "帮我查", "下单")

# ── 确认判定 ──────────────────────────────────────────────────────────
# 现在的主路径是**结构化**的：挂起时把 intent_id 下发给前端，前端在用户点
# 「确认」时原样带回，后端比对通过才执行。这样「确认」这个动作离开了自然语言，
# 不会再出现「好的，但我想改时间」被当成确认的情况。
#
# 下面这组关键词只服务于**过渡期**：前端还没改造时，退回到原来靠词猜的行为，
# 保证不破坏现有前端。每次命中都会打 warning。
# ⚠️ 前端改造上线后，把环境变量 AGENT_CONFIRM_LEGACY 置 0，缺陷才算真正消除。
LEGACY_CONFIRM_WORDS = ("确认", "是的", "可以", "好的", "就买", "下单", "买吧")
LEGACY_PAY_WORDS = ("支付", "付款", "付钱")


def _legacy_confirm_enabled() -> bool:
    return os.getenv("AGENT_CONFIRM_LEGACY", "1") == "1"


def _intent_decision(intent: dict, user_text: str, resume: Optional[dict]) -> bool:
    """判定「是否确认下单」。

    结构化优先；前端未回传 intent_id 时才考虑过渡期的关键词回退。
    """
    if resume is not None:
        # 没有 intent_id 的载荷不是针对「下单」的（例如支付载荷）—— 不处理
        if "intent_id" not in resume:
            return False
        matched = resume.get("intent_id") == intent.get("intent_id")
        if not matched:
            logger.warning("确认载荷 intent_id 不匹配，已拒绝（串话或重放）")
        return bool(resume.get("confirm")) and matched

    if _legacy_confirm_enabled():
        hit = any(w in user_text for w in LEGACY_CONFIRM_WORDS)
        if hit:
            logger.warning("下单确认走了关键词回退（AGENT_CONFIRM_LEGACY=1）——"
                           "前端改造上线后请把该开关置 0")
        return hit
    return False


def _pay_decision(user_text: str, resume: Optional[dict]) -> bool:
    """判定「是否确认支付」。

    ⚠️ 必须要求载荷**显式声明** action=pay。
    曾经的写法是「只要 resume 里 confirm 为真就付」，结果任何带 confirm 的载荷
    —— 包括一个过期的下单确认 —— 都会顺带把待支付订单付掉。
    付款是不可逆动作，不能靠载荷里恰好有个 confirm 字段来触发。
    """
    if resume is not None:
        if resume.get("action") != "pay":
            logger.warning("收到非支付用途的确认载荷，已忽略支付路径：%s", resume)
            return False
        return bool(resume.get("confirm"))

    if _legacy_confirm_enabled():
        return any(w in user_text for w in LEGACY_PAY_WORDS)
    return False


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


async def _safe_stations() -> list[dict]:
    """取车站列表；失败不阻断这一轮 —— 车站只是给模型的提示，不是必需项。"""
    try:
        return await java_client.stations()
    except Exception:  # noqa: BLE001
        logger.exception("车站服务不可用")
        return []


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
        # 挂起状态（最近查票结果 / 待确认意向 / 待支付订单）已挪出进程内存，
        # 落到 Redis —— 见 app/agent/pending.py。
        # 原来这里有三个按 user_id 索引的 dict，多副本部署会串号、重启会丢。

    def _rag_store(self) -> Optional[RagStore]:
        if self._rag is None:
            self._rag = get_store() if rag_available() else None
        return self._rag

    async def _retrieve_refs(self, user_text: str, user) -> list[str]:
        """每轮检索参考资料注入上下文；失败只降级不报错。

        抽出来是为了能和取车站并行跑（见 handle）。
        """
        rag = self._rag_store()
        if not rag or not rag.enabled:
            return []
        try:
            return await rag.retrieve(user_text, department=user.department, top_k=4)
        except Exception as e:  # noqa: BLE001
            logger.warning("RAG 检索失败，跳过参考注入：%s", e)
            return []

    async def handle(self, user_text: str,
                     resume: Optional[dict] = None) -> AsyncIterator[dict]:
        """跑一轮对话。

        resume: 前端在用户点「确认 / 取消」时回传的结构化载荷，
                形如 {"confirm": True, "intent_id": "a1b2c3d4e5f6"}。
                普通文本输入时为 None —— 此时不再靠关键词猜意图（过渡期除外）。
        """
        user = current_user()
        uid = user.user_id
        yield _event("think", f"已收到请求，正在为您办理（{user.identity}）。")

        # ── 两段式确认：先判，命中就返回 ──
        # 放在取车站 / RAG 之前：确认路径用不到这两样，挪前面省掉一轮无用 IO。
        pay_rid = pending.get(uid, "order")
        if pay_rid and _pay_decision(user_text, resume):
            pending.drop(uid, "order")
            try:
                r = await java_client.pay(pay_rid)
                yield _event("result", f"订单 {pay_rid} 已支付，状态 {r.get('status')}。")
            except java_client.JavaError as e:
                yield _event("error", e.msg)
            return

        intent = pending.get(uid, "intent")
        if intent and _intent_decision(intent, user_text, resume):
            # 用户确认：执行预填的下单（确定性路径，不再让模型介入）
            pending.drop(uid, "intent")
            async for ev in self._commit_buy(user, intent):
                yield ev
            return
        if intent:
            # 用户改了主意 / 提了新请求：作废旧意向，落到下面按新请求处理
            pending.drop(uid, "intent")

        if resume is not None and not user_text.strip():
            # 结构化载荷没匹配上任何待办（多半是过期或串话过来的）。
            # 此时没有自然语言输入，不必再拿空消息去调模型白花一次调用。
            yield _event("answer", "该操作已失效或与当前待办不符，请重新发起。")
            return

        # 车站列表与 RAG 检索互不依赖 —— 并行取，省一个往返。
        stations, refs = await asyncio.gather(
            _safe_stations(),
            self._retrieve_refs(user_text, user),
        )
        station_line = "、".join(s["name"] for s in stations[:30])
        ref_block = ("【参考资料】\n" + "\n---\n".join(r.strip() for r in refs if r and r.strip())
                     if refs else "【参考资料】（未检索到相关资料）")

        # 记忆按会话隔离：上下文取自“当前会话”，切会话即切上下文
        session = hist.current_session(uid)
        state = mem.load_state(uid, session=session)

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
                        pending.set(uid, "rows", rows)
                        shown = True
                        acted = True
                        text = self._query_text(rows)
                        msgs.append({"role": "assistant", "content": f"查票结果：\n{text}"})
                        yield _event("result", text)
                    elif name == "buy_ticket":
                        # 防臆造 + 反向复述：tripId 必须来自查票结果，且下单前请用户确认
                        row = _find_trip(pending.get(uid, "rows") or [], args.get("tripId"))
                        if row is None:
                            yield _event("error", "请先使用 query_tickets 查询车次，再从结果中选择要购买的车次。")
                            return
                        intent = {
                            # 结构化确认用的标识：下发给前端，用户点「确认」时原样带回，
                            # 后端比对通过才执行 —— 不再靠从「好的」这类词里猜意图。
                            "intent_id": pending.new_intent_id(),
                            "tripId": row["tripId"],
                            "seatClass": row["seatClass"],
                            "from": row["from"],
                            "to": row["to"],
                            "date": row.get("travelDate"),
                            "trainCode": row.get("trainCode"),
                            "ticketType": (args.get("ticketType") or "ADULT").upper(),
                            "contactId": args.get("contactId"),
                        }
                        pending.set(uid, "intent", intent)
                        shown = True
                        acted = True
                        base = (f"请确认下单：{intent['trainCode']} {intent['date']} "
                                f"{intent['from']}→{intent['to']} {intent['seatClass']} "
                                f"({intent['ticketType']})。请在下方确认或取消。")
                        if intent["ticketType"] in ("CHILD", "STUDENT"):
                            rule = await self._rule_snippet(
                                "儿童票" if intent["ticketType"] == "CHILD" else "学生票")
                            if rule:
                                base = f"下单前请核对规则：{rule[:180]}\n{base}"
                        confirm = base
                        msgs.append({"role": "assistant", "content": confirm})
                        # intent_id 随事件下发：前端保存它，用户点「确认」时原样带回。
                        yield {**_event("confirm", confirm),
                               "intent_id": intent["intent_id"]}
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
                ticket_type=intent.get("ticketType", "ADULT"),
                contact_id=intent.get("contactId"))
        except java_client.JavaError as e:
            yield _event("error", e.msg)
            return
        rid = r.get("requestId")
        if rid:
            pending.set(user.user_id, "order", rid)
        line = (f"下单成功：{r.get('from')}->{r.get('to')} {r.get('seatClass')}，"
                f"金额 {r.get('priceCents')}分，状态 {r.get('status')}，订单号 {r.get('orderNo')}。"
                f"请在下方完成支付。")
        # 带上 requestId：前端据此渲染「支付」按钮，不必让用户打字。
        # 这也让支付脱离了关键词路径 —— 关掉 AGENT_CONFIRM_LEGACY 后仍可支付。
        yield {**_event("result", line), "pay_request_id": rid}

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
