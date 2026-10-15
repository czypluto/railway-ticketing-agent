"""对话入口：POST /api/chat，以 SSE 流式返回编排事件。"""
import json
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent import history as hist
from ..agent import memory as mem
from ..agent.orchestrator import AgentService
from ..middleware import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["智能助手"])

_agent = AgentService()


class ChatBody(BaseModel):
    message: str = Field(default="", description="用户自然语言请求；点确认/取消按钮时可为空")
    resume: Optional[dict] = Field(
        default=None,
        description='结构化确认载荷，形如 {"confirm": true, "intent_id": "a1b2c3d4e5f6"}；'
                    '普通文本输入时为 null')


async def _stream(body: ChatBody):
    """逐步推送：思考中 -> 检索/校验 -> 执行 -> 结果。每条事件带当前 model。"""
    if not body.message.strip() and not body.resume:
        payload = {"kind": "error", "text": "请求为空：需要 message 或 resume 之一。", "model": ""}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # 归档用的用户侧文案：点按钮那一轮没有自然语言输入，补一个可读的占位
    user_note = body.message.strip() or (
        f"［{'确认' if body.resume.get('confirm') else '取消'}下单］")

    last = None
    lines: list[str] = []
    try:
        uid = current_user().user_id
    except Exception:  # noqa: BLE001
        uid = None
    try:
        async for ev in _agent.handle(body.message, body.resume):
            # 归档只收「有内容的终态」事件；实际会出现的 kind 只有 think/result/answer/confirm/error
            if ev.get("kind") in ("result", "answer", "confirm"):
                last = ev.get("text")
                lines.append(ev.get("text") or "")
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    except PermissionError as e:
        ev = {"kind": "error", "text": str(e), "model": ""}
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    except Exception as e:  # noqa: BLE001
        logger.exception("编排异常")
        ev = {"kind": "error", "text": f"处理出错：{e}", "model": ""}
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    finally:
        if uid:
            try:
                # 1) 全量对话档案（可回溯）
                hist.append_turn(uid, user_note, "\n".join(x for x in lines if x))
                # 2) 当前对话记忆（按会话分片，供切回该会话时续聊）
                #    append 是协程：超窗时要走 LLM 压缩摘要
                if last:
                    await mem.append(uid, user_note, last,
                                     session=hist.current_session(uid))
            except Exception:  # noqa: BLE001
                logger.warning("对话记录/记忆写入失败")
        yield "data: [DONE]\n\n"


@router.post("/chat", summary="对话", description="自然语言 -> 意图识别 -> 调 Java 查票/购票/订单，SSE 流式返回阶段事件")
async def chat(body: ChatBody):
    return StreamingResponse(
        _stream(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
