"""工具契约回归测试：声明了工具，主循环里就必须有对应分发分支。

工具契约分散在三处——`tools.py` 的 schema、`orchestrator.py` 的分发分支、
`java_client.py` 的 REST 调用。三处靠人工对齐，漏掉一个的表现是
**「模型调用后静默无响应」**：不报错、不抛异常，只是那一轮什么都没发生，
日志里也看不出来。所以用源码扫描把它钉住。

（纯逻辑与源码扫描，不需要启动服务。）
"""
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP.parent))

from app.tools import all_schemas  # noqa: E402

ORCHESTRATOR = APP / "agent" / "orchestrator.py"


def declared_tools() -> set[str]:
    """`tools.py` 里声明给模型的全部工具名。"""
    return {t["function"]["name"] for t in all_schemas()}


def dispatched_tools() -> set[str]:
    """从 orchestrator 的分发链里提取已实现的工具名。

    支持两种写法：`name == "x"` 与 `name in ("x", "y", ...)`。
    """
    src = ORCHESTRATOR.read_text(encoding="utf-8")
    names = set(re.findall(r'name == "([a-z_]+)"', src))
    for group in re.findall(r"name in \(([^)]*)\)", src):
        names |= set(re.findall(r'"([a-z_]+)"', group))
    return names


def test_every_declared_tool_has_a_dispatch_branch():
    missing = declared_tools() - dispatched_tools()
    assert not missing, (
        f"这些工具已声明给模型、却没有分发分支，调用后会静默无响应：{sorted(missing)}")


def test_no_dispatch_branch_without_declaration():
    extra = dispatched_tools() - declared_tools()
    assert not extra, f"这些分发分支没有对应的工具声明（模型永远不会调用）：{sorted(extra)}"


def test_schemas_are_wellformed():
    for t in all_schemas():
        assert t["type"] == "function"
        fn = t["function"]
        assert fn["name"], "工具名不能为空"
        assert fn["description"], f'{fn["name"]} 缺少 description（模型靠它决定是否调用）'
        params = fn["parameters"]
        assert params["type"] == "object"
        for req in params.get("required", []):
            assert req in params["properties"], (
                f'{fn["name"]} 的 required 字段 {req} 不在 properties 里')
