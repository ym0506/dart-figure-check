"""표 칸 검사를 MCP 도구로 내놓는다(stdio).

    .venv/bin/pip install -r requirements-mcp.txt
    .venv/bin/python -m dartcheck.mcp_server            # MCP 서버(stdio)
    .venv/bin/python -m dartcheck.mcp_server replay     # 저장된 LLM 값을 서버에 다시 넣어 판정이 같은지 확인

도구 셋:
- list_reports: 내려받아 둔 사업보고서 목록
- find_rows: 한 보고서의 표에서 행 후보를 BM25로 찾는다(위치 표시와 줄 글을 돌려준다)
- check_figure: LLM이 낸 값 하나(계정, 기간, 값, 인용)를 인용 검사(P1)와 표 칸 검사(P2)로 본다

에이전트는 find_rows로 줄을 읽고 값을 고른 뒤 check_figure를 통과한 값만 쓴다. 금액은 LLM이 말한 단위가 아니라
검사기가 표 칸에서 읽은 숫자와 표의 단위로 계산해 돌려준다. 보고서는 data/raw/에 내려받아 둔 것만 읽는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

from .docs import Report, is_consolidated_statement, is_summary, label_ok, load_report
from .retrieve import BM25, QUERY, row_chunks_label
from .verify import Checker

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
RES = Path(__file__).resolve().parent.parent / "results"
ACCOUNTS = tuple(QUERY)
PERIODS = ("당기", "전기")
SAMPLES = {"sample": "개발용", "holdout": "평가용"}



class Verdict(TypedDict):
    quote_check: bool
    cell_check: bool
    reason: str
    amount_won: str | None


_reports: dict[str, Report] = {}
_checkers: dict[str, Checker] = {}


def _index() -> list[dict]:
    out = []
    for name, label in SAMPLES.items():
        f = RAW / f"{name}.json"
        if not f.exists():
            continue
        for m in json.loads(f.read_text(encoding="utf-8"))["chosen"]:
            out.append({"rcept_no": m["rcept_no"], "corp_name": m["corp_name"], "report": m.get("report_nm", ""),
                        "sample": label})
    return out


def _report(rcept_no: str) -> Report:
    if rcept_no not in _reports:
        folder = RAW / rcept_no
        if not rcept_no.isdigit() or not folder.is_dir():
            raise ValueError(f"내려받아 둔 보고서가 아닙니다: {rcept_no}. list_reports로 접수번호를 확인하세요.")
        _reports[rcept_no] = load_report(folder)
    return _reports[rcept_no]


def _checker(rcept_no: str) -> Checker:
    if rcept_no not in _checkers:
        _checkers[rcept_no] = Checker(_report(rcept_no))
    return _checkers[rcept_no]


def list_reports() -> list[dict]:
    """내려받아 둔 사업보고서 목록. 접수번호(rcept_no)를 다른 도구에 넘긴다."""
    return _index()


def find_rows(rcept_no: str, query: str, k: int = 5, statements_only: bool = True) -> list[dict]:
    """보고서의 '재무에 관한 사항' 표에서 행을 찾는다.

    query는 계정 이름(매출액, 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계)이나 자유 검색어.
    계정 이름이면 행 이름이 정확히 맞는 행을 먼저, 나머지는 BM25 순서로 준다.
    statements_only가 참이면 요약재무정보와 연결재무제표 섹션의 표만 본다.
    돌려주는 row(T12R3 같은 위치)와 line(그 줄의 글)을 check_figure의 row와 quote에 그대로 쓸 수 있다.
    """
    rep = _report(rcept_no)
    tables = [t for t in rep.tables if not statements_only or is_consolidated_statement(t) or is_summary(t)]
    chunks = row_chunks_label(tables)
    if not chunks:
        return []
    lines = _checker(rcept_no).lines
    by_tid = {t.tid: t for t in tables}
    k = max(1, min(int(k), 20))
    where = [next(iter(c.rows)) for c in chunks]
    # 계정 이름으로 물으면 행 이름이 그 계정의 이름 목록과 정확히 같은 행을 문서 순서대로 먼저 준다.
    first = [i for i, (tid, r) in enumerate(where) if query in ACCOUNTS and label_ok(query, by_tid[tid].row_label(r))]
    rest = [i for i in BM25([c.text for c in chunks]).search(QUERY.get(query, query), k) if i not in first]
    out = []
    for i in (first + rest)[:k]:
        tid, r = where[i]
        t = by_tid[tid]
        out.append({"row": f"{tid}R{r}", "line": lines.get(f"{tid}R{r}", ""), "label": t.row_label(r),
                    "where": t.path, "unit": t.unit_text or ""})
    return out


def check_figure(rcept_no: str, account: str, period: str, value: str, quote: str, row: str = "") -> Verdict:
    """LLM이 낸 값 하나를 받을지 판단한다.

    account는 여섯 계정 중 하나, period는 당기 또는 전기, value는 표에 적힌 숫자 그대로(예: 25,810,082),
    quote는 원문 줄에서 글자 그대로 옮긴 인용, row는 그 줄의 위치(없어도 됨).
    quote_check는 인용이 원문에 있고 그 안에 같은 숫자가 있는지, cell_check는 그 줄이 연결재무제표 표의
    그 계정 행이고 그 기간 열의 숫자가 같은지다. cell_check가 참일 때만 amount_won(원 단위 금액)을 준다.
    """
    if account not in ACCOUNTS:
        raise ValueError(f"account는 {', '.join(ACCOUNTS)} 중 하나여야 합니다.")
    if period not in PERIODS:
        raise ValueError("period는 당기 또는 전기여야 합니다.")
    chk = _checker(rcept_no)
    item = {"value": value, "quote": quote, "row": row}
    p1, why1 = chk.p1(item)
    p2, why2, won = chk.p2(item, account, period)
    return {"quote_check": p1, "cell_check": p2, "reason": why2 or why1,
            "amount_won": str(int(won)) if p2 and won is not None else None}


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "dart-figure-check",
        instructions="사업보고서 표에서 뽑은 숫자를 쓰기 전에 check_figure로 확인하세요. "
                     "cell_check가 거짓이면 그 값은 쓰지 말고 reason을 사용자에게 알리세요.")
    for fn in (list_reports, find_rows, check_figure):
        server.tool()(fn)
    return server


def replay() -> dict:
    """저장된 LLM 값(results/llm_2*.json)을 stdio로 띄운 서버의 check_figure에 다시 넣고,
    실험 때의 판정(results/llm_items_2*.json)과 같은지 센다."""
    import asyncio
    from collections import Counter

    from mcp import Client, StdioServerParameters

    async def go():
        want, got = Counter(), Counter()
        calls = 0
        async with Client(StdioServerParameters(command=sys.executable, args=["-m", "dartcheck.mcp_server"],
                                                cwd=str(RAW.parent.parent))) as c:
            tools = [t.name for t in (await c.list_tools()).tools]
            known = {m["rcept_no"] for m in _index()}
            for f in sorted(RES.glob("llm_2*.json")):
                saved = RES / f.name.replace("llm_", "llm_items_")
                if not saved.exists():
                    continue
                rows = json.loads(saved.read_text(encoding="utf-8"))
                for r in rows["rows"] if isinstance(rows, dict) else rows:
                    want[(r["corp"], r["account"], r["period"], str(r["value"]), r["p1"], r["p2"])] += 1
                for run in json.loads(f.read_text(encoding="utf-8"))["runs"]:
                    if run.get("error") or run["rcept_no"] not in known:
                        continue
                    for x in run["items"]:
                        if not x.get("found") or x.get("account") not in ACCOUNTS or x.get("period") not in PERIODS:
                            continue
                        res = await c.call_tool("check_figure", {
                            "rcept_no": run["rcept_no"], "account": x["account"], "period": x["period"],
                            "value": str(x.get("value", "")), "quote": str(x.get("quote", "")),
                            "row": str(x.get("row", ""))})
                        calls += 1
                        d = res.structured_content or json.loads(res.content[0].text)
                        got[(run["corp"], x["account"], x["period"], str(x.get("value")), d["quote_check"],
                             d["cell_check"])] += 1
        return {"tools": tools, "calls": calls, "saved": sum(want.values()),
                "same": sum((want & got).values()), "different": sum((want - got).values()) + sum((got - want).values())}

    out = asyncio.run(go())
    (RES / "mcp_replay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "replay":
        replay()
    else:
        build_server().run()
