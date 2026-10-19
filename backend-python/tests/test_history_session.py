"""会话 id 白名单校验：URL 来的 session 不能直接拼进文件名。

Starlette 的 `/{session}` 默认不匹配 "/"，但百分号编码的 %2F 在部分版本会先解码
再匹配路由。这里钉住「形状不对就当不存在」，不再指望路由层兜住。
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import history as hist  # noqa: E402


def test_generated_session_ids_are_accepted():
    for _ in range(50):
        assert hist.valid_session("s" + uuid.uuid4().hex[:10])


def test_bad_shapes_are_rejected():
    bad = [
        "", None, ".", "..", "s/../x", "s..", "../../etc/passwd",
        "s0123456789.json",          # 带后缀
        "s" + "0" * 9,               # 短一位
        "s" + "0" * 11,              # 长一位
        "S" + "a" * 10,              # 大写前缀
        "s" + "z" * 10,              # 非十六进制
    ]
    for s in bad:
        assert not hist.valid_session(s), s


def test_path_is_none_for_invalid_session():
    assert hist._path(1, "../../x") is None
    assert hist._path(1, "s0123456789") is not None


def test_session_apis_are_noops_for_invalid_session():
    """形状非法时一律按"不存在"处理，不去碰文件系统、也不抛异常。"""
    assert hist.activate(1, "../../x") is False
    assert hist.get_session(1, "../../x") == []
    hist.delete_session(1, "../../x")  # 不应抛错
