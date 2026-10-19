"""站名解析：歧义必须报错，不能静默取第一条。

原实现 `rows[0]["id"]`：一旦库里同时有"北京南/北京西/北京丰台"，
用户说"北京"就会落到第一条 —— 查的是哪一趟、买的是哪一张，
用户和模型都看不出来。宁可让模型回头问一句。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import java_client  # noqa: E402


def _patch_stations(rows, monkeypatch):
    async def fake(kw=None):
        return rows

    monkeypatch.setattr(java_client, "stations", fake)


def test_exact_match_wins(monkeypatch):
    _patch_stations([{"id": 1, "name": "北京南"}, {"id": 2, "name": "北京西"}], monkeypatch)
    assert asyncio.run(java_client._resolve_station("北京南")) == 1


def test_unique_fuzzy_match_is_accepted(monkeypatch):
    """库里只有一个"北京南"时，用户说"北京"应当命中。"""
    _patch_stations([{"id": 7, "name": "北京南"}], monkeypatch)
    assert asyncio.run(java_client._resolve_station("北京")) == 7


def test_ambiguous_fuzzy_match_is_rejected(monkeypatch):
    """★ 回归：歧义时说清楚，不要猜。"""
    _patch_stations([{"id": 1, "name": "北京南"},
                     {"id": 2, "name": "北京西"},
                     {"id": 3, "name": "北京丰台"}], monkeypatch)
    try:
        asyncio.run(java_client._resolve_station("北京"))
    except java_client.JavaError as e:
        assert "不唯一" in e.msg
        assert "北京西" in e.msg      # 要把候选列给用户，否则用户不知道怎么改口
    else:
        raise AssertionError("歧义站点必须报错，不能猜")


def test_missing_station_is_rejected(monkeypatch):
    _patch_stations([], monkeypatch)
    try:
        asyncio.run(java_client._resolve_station("火星站"))
    except java_client.JavaError as e:
        assert "未找到" in e.msg
    else:
        raise AssertionError("空结果必须报错")
