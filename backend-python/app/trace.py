"""请求级 trace id：让一次对话的 Python 与 Java 日志能用同一个 id 串起来。

**为什么需要**：一次「下单」横跨 Python 编排层与 Java 交易层，日志落在两处。
出问题时只能靠时间戳猜，跨服务根本对不上号。trace id 沿 HTTP 头透传，
两边日志都能按同一个 id grep 出完整链路。

**为什么用 contextvar 而不是参数传递**：否则要把它一路塞进 `java_client`、
`rag`、`memory` 的每个函数签名，侵入性太大。contextvar 在同一个 async 任务里
自动可见，调用方零改动。

**边界**：只在单进程内有效。跨进程（uvicorn 多 worker）不共享，
但 trace id 本来就是每请求新生成的，没有共享需求。
"""
import contextvars
import uuid

# 跨服务透传用的 HTTP 头名。Java 侧由 TraceIdFilter 读取同一个名字。
HEADER = "X-Trace-Id"

_current: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """生成一个新的 trace id（16 位十六进制，够用且日志里不占地方）。"""
    return uuid.uuid4().hex[:16]


def set_trace_id(tid: str) -> None:
    _current.set(tid or "")


def current() -> str:
    """当前请求的 trace id；不在请求上下文里时返回空串。"""
    return _current.get()
