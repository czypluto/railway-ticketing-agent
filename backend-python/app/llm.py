"""模型路由层：统一 chat 接口，按配置把任务分发到指定模型（OpenAI 兼容协议）。

调用方只传 task 名称（agent/multimodal/gen），不传模型名。
"""
import logging
from typing import Any, Optional

import httpx

from . import config
from . import usage as usage_log

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """连接复用：进程内共享一个 AsyncClient（与 java_client.py 同一做法）。

    原实现是每次 chat() 都 `async with httpx.AsyncClient(...)`，
    意味着**每次模型调用都要重做一遍到服务商的 TLS 握手**，而一轮对话要调 1~4 次。
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=90)
    return _client


class LlmError(Exception):
    pass


class ToolCall:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments  # JSON 字符串

    def arg_dict(self) -> dict:
        import json

        try:
            return json.loads(self.arguments) if self.arguments else {}
        except json.JSONDecodeError:
            return {}


class ChatOut:
    def __init__(self, content: str, tool_calls: Optional[list[ToolCall]] = None,
                 model: str = "", usage: Optional[dict] = None):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.model = model
        # 接口返回的用量（prompt_tokens/completion_tokens/total_tokens）。
        # 有些兼容实现不返回，缺失时给空 dict，由调用方按 0 记。
        self.usage = usage or {}


async def chat(
    task: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.2,
) -> ChatOut:
    """task 指定业务用途，模型名由路由配置解析，返回文本与工具调用。"""
    if not config.DEEPSEEK_API_KEY:
        raise LlmError("未配置 DEEPSEEK_API_KEY")
    model = config.resolve_model(task)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tool_choice"] = "auto"
        payload["tools"] = tools

    url = f"{config.DEEPSEEK_BASE}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}
    try:
        resp = await _get_client().post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise LlmError(f"模型接口返回 {resp.status_code}：{resp.text[:300]}")
        data = resp.json()
    except httpx.HTTPError as e:
        logger.error("模型调用失败 task=%s: %s", task, e)
        raise LlmError("模型服务暂不可用，请稍后再试") from e

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LlmError("模型返回异常") from e

    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        tool_calls.append(ToolCall(fn.get("name", ""), fn.get("arguments", "")))

    out = ChatOut(content=msg.get("content") or "", tool_calls=tool_calls, model=model,
                  usage=data.get("usage") or {})
    # 记账放在这里而不是各调用点：所有调用都会被覆盖，将来新增的也不会漏。
    # 失败只记 warning，不影响主流程。
    entry = usage_log.record(task=task, model=model, usage=out.usage)
    logger.debug("模型用量 task=%s model=%s tokens=%s",
                 task, model, entry.get("totalTokens"))
    return out
