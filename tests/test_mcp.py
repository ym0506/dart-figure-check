import asyncio

import pytest

from dartcheck import mcp_server as ms
from dartcheck.docs import Report
from dartcheck.parse import build_tree, tables_with_context
from tests.test_core import DOC


@pytest.fixture
def fake_report(monkeypatch):
    tables = [t for t in tables_with_context(build_tree(DOC)) if "재무에 관한 사항" in t.path]
    monkeypatch.setitem(ms._reports, "1", Report("1", "시험회사", tables, {}, {}))
    monkeypatch.delitem(ms._checkers, "1", raising=False)
    yield
    ms._checkers.pop("1", None)


def test_find_rows_puts_the_exact_label_first(fake_report):
    rows = ms.find_rows("1", "자산총계")
    assert rows[0]["label"] == "자산총계"
    assert "514,531,948" in rows[0]["line"]


def test_check_figure_accepts_the_cell_and_rejects_the_other_period(fake_report):
    row = ms.find_rows("1", "자산총계")[0]
    ok = ms.check_figure("1", "자산총계", "당기", "514,531,948", row["line"], row["row"])
    assert ok == {"quote_check": True, "cell_check": True, "reason": "", "amount_won": "514531948000000"}
    # 같은 인용 안에 있는 전기 숫자를 당기로 내면 인용 검사는 통과하고 표 칸 검사에서 걸린다
    bad = ms.check_figure("1", "자산총계", "당기", "455,905,980", row["line"], row["row"])
    assert bad["quote_check"] and not bad["cell_check"] and bad["amount_won"] is None
    assert "당기 열" in bad["reason"]


def test_check_figure_rejects_unknown_account(fake_report):
    with pytest.raises(ValueError):
        ms.check_figure("1", "현금", "당기", "1", "x")


def test_server_lists_and_calls_tools(fake_report):
    pytest.importorskip("mcp")
    from mcp import Client

    async def go():
        async with Client(ms.build_server()) as c:
            names = [t.name for t in (await c.list_tools()).tools]
            row = ms.find_rows("1", "자산총계")[0]
            res = await c.call_tool("check_figure", {"rcept_no": "1", "account": "자산총계", "period": "전기",
                                                     "value": "455,905,980", "quote": row["line"]})
            return names, res.structured_content

    names, out = asyncio.run(go())
    assert names == ["list_reports", "find_rows", "check_figure"]
    assert out["cell_check"] is True and out["amount_won"] == "455905980000000"
