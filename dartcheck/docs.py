"""내려받은 사업보고서 한 건을 읽어 '재무에 관한 사항' 표들과 정답 칸 위치를 준비한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .gold import norm_label, pick_gold
from .numbers import parse_number, same_amount
from .parse import Table, build_tree, period_columns, render_table, tables_with_context

LABEL_KEYS = {
    "매출액": ("매출", "수익", "영업수익"),
    "영업이익": ("영업이익", "영업손익", "영업손실"),
    "당기순이익": ("당기순", "순이익", "순손익", "순손실"),
    "자산총계": ("자산총계", "자산합계"),
    "부채총계": ("부채총계", "부채합계"),
    "자본총계": ("자본총계", "자본합계"),
}


def read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def is_consolidated_statement(t: Table) -> bool:
    """'2. 연결재무제표' 섹션의 표(주석 제외)."""
    parts = t.path.split(" > ")
    return any("연결재무제표" in p and "주석" not in p for p in parts) and "주석" not in t.path


def is_summary(t: Table) -> bool:
    return "요약재무정보" in t.path


LABEL_EXACT = {
    "매출액": {"매출액", "수익(매출액)", "영업수익", "매출", "수익"},
    "영업이익": {"영업이익", "영업이익(손실)", "영업손익", "영업손실"},
    "당기순이익": {"당기순이익", "당기순이익(손실)", "당기순손익", "당기순손실", "연결당기순이익",
                 "연결당기순이익(손실)", "연결당기순손익"},
    "자산총계": {"자산총계", "자산합계"},
    "부채총계": {"부채총계", "부채합계"},
    "자본총계": {"자본총계", "자본합계"},
}


def strict_label(label: str) -> str:
    n = norm_label(label)
    return re.sub(r"\(주[\d,\s.]*\)", "", n)


def label_ok(account: str, label: str, strict: bool = True) -> bool:
    """행 이름이 계정과 맞는지. strict(기본)는 주석 번호를 뗀 뒤 이름 목록과 정확히 같아야 한다.
    strict=False는 처음 만든 부분 문자열 비교(예: '매출총이익'도 '매출'을 포함해 매출액으로 통과)로, 비교용으로만 남긴다."""
    if strict:
        return strict_label(label) in LABEL_EXACT[account]
    n = norm_label(label)
    return any(k in n for k in LABEL_KEYS[account])


@dataclass
class Report:
    rcept_no: str
    corp_name: str
    tables: list[Table]          # '재무에 관한 사항' 안의 표 전부(주석, 별도 포함)
    gold: dict
    gold_cells: dict             # {계정: {"당기": (tid, row, col), "전기": ...}}

    def table(self, tid: str) -> Table | None:
        return next((t for t in self.tables if t.tid == tid), None)

    def lines(self, tables: list[Table]) -> list[str]:
        out = []
        for t in tables:
            out.extend(render_table(t))
        return out


def find_gold_cells(tables: list[Table], gold: dict) -> dict:
    """연결재무제표 표에서 정답 금액과 반올림 범위 안에서 같고 행 이름도 맞는 칸을 찾는다(코드만, LLM 없음)."""
    out: dict = {}
    for acct, g in gold.items():
        for t in [x for x in tables if is_consolidated_statement(x)]:
            if not t.unit:
                continue
            cols = period_columns(t)
            if "당기" not in cols:
                continue
            for r in range(t.header_rows, len(t.grid)):
                if not label_ok(acct, t.row_label(r)):
                    continue
                v = parse_number(t.grid[r][cols["당기"]])
                if v is not None and same_amount(v, t.unit, Decimal(g["당기"])):
                    cells = {"당기": (t.tid, r, cols["당기"])}
                    if "전기" in cols:
                        cells["전기"] = (t.tid, r, cols["전기"])
                    out[acct] = cells
                    break
            if acct in out:
                break
    return out


def load_report(folder: Path) -> Report:
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    main = folder / f"{meta['rcept_no']}.xml"
    if not main.exists():   # 본문 파일 이름이 다르면 가장 큰 xml
        main = max(folder.glob("*.xml"), key=lambda p: p.stat().st_size)
    root = build_tree(read_text(main))
    tables = [t for t in tables_with_context(root) if "재무에 관한 사항" in t.path]
    rows = json.loads((folder / "gold_rows.json").read_text(encoding="utf-8"))
    gold = pick_gold(rows)
    return Report(meta["rcept_no"], meta["corp_name"], tables, gold, find_gold_cells(tables, gold))


def gold_rows(rep: "Report", acct: str) -> set:
    """정답 금액(당기)이 들어 있는 행 전부: 연결재무제표 표와 요약재무정보 표에서 행 이름이 맞고 당기 칸이 정답과 같은 행."""
    g = rep.gold.get(acct)
    out = set()
    if not g:
        return out
    for t in rep.tables:
        if not (is_consolidated_statement(t) or is_summary(t)) or not t.unit:
            continue
        cols = period_columns(t)
        if "당기" not in cols:
            continue
        for r in range(t.header_rows, len(t.grid)):
            v = parse_number(t.grid[r][cols["당기"]])
            if v is not None and label_ok(acct, t.row_label(r)) and same_amount(v, t.unit, Decimal(g["당기"])):
                out.add((t.tid, r))
    return out
