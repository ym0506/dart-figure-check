"""LLM이 낸 값 하나를 받을지 판단하는 두 가지 검사.

P1 글자와 숫자 대조: 인용이 원문 줄에 글자 그대로 있고 그 인용 안에 같은 숫자가 있으면 받는다.
   최종 값은 LLM이 말한 값과 단위로 계산한다. (앞선 10-K 프로젝트와 공고 대조 도구에서 쓴 방식)
P2 표 칸 확인 추가: P1에 더해 LLM이 가리킨 표가 연결재무제표(또는 요약재무정보의 연결 부분)이고,
   그 행의 '당기' 또는 '전기' 열(머리글의 기수·연도로 코드가 정함)에 있는 숫자가 LLM이 말한 값과 같아야 받는다.
   LLM이 적은 위치(T3R5)는 자주 틀려서, 인용과 글자가 똑같은 줄이 있으면 코드가 그 줄의 위치로 바꿔 본다
   (같은 줄이 여러 개면 연결재무제표 표의 줄을 먼저 고른다). 그래서 '어느 표를 가리켰나'는 LLM의 위치가 아니라 인용으로 정해진다.
   최종 값은 코드가 그 칸에서 읽은 숫자와 표의 단위로 계산한다. LLM이 말한 단위는 쓰지 않는다.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .docs import Report, is_consolidated_statement, is_summary, label_ok
from .numbers import UNIT_FACTOR, numbers_in, numbers_unsigned, parse_number, same_amount
from .parse import period_columns, render_table


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _dec(s: str) -> Decimal | None:
    v = parse_number(str(s))
    if v is not None:
        return v
    try:
        return Decimal(str(s).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


class Checker:
    def __init__(self, rep: Report):
        self.rep = rep
        self.lines = {}
        for t in rep.tables:
            for line in render_table(t)[1:]:
                rid, _, body = line.partition("] ")
                self.lines[rid.lstrip("[")] = _ws(body)
        self.all_text = "\n".join(self.lines.values())

    def p1(self, item: dict, signed: bool = True) -> tuple[bool, str]:
        q = _ws(item.get("quote", ""))
        if not q or q not in self.all_text:
            return False, "인용이 원문에 없음"
        v = _dec(item.get("value", ""))
        if v is None:
            return False, "값을 숫자로 못 읽음"
        nums = numbers_in(q) if signed else numbers_unsigned(q)
        if (v if signed else abs(v)) not in nums:
            return False, "인용 안에 그 숫자가 없음"
        return True, ""

    def p1_value_won(self, item: dict) -> Decimal | None:
        v = _dec(item.get("value", ""))
        f = UNIT_FACTOR.get(re.sub(r"\s+", "", item.get("unit", "")))
        return None if v is None or f is None else v * f

    def p2(self, item: dict, account: str, period: str, strict: bool = True) -> tuple[bool, str, Decimal | None]:
        ok, why = self.p1(item)
        if not ok:
            return False, why, None
        rid = str(item.get("row", ""))
        q = _ws(item.get("quote", ""))
        # 위치 표시는 LLM이 자주 틀리므로 인용과 글자가 같은 줄을 코드가 다시 찾는다.
        # LLM이 적은 위치가 그 줄 중 하나면 그대로, 아니면 연결재무제표 표의 줄을 우선해 하나를 고른다.
        same = [k for k, v in self.lines.items() if v == q]
        if same and rid not in same:
            pref = [k for k in same if is_consolidated_statement(self.rep.table(k.split("R")[0]))]
            rid = (pref or same)[0]
        m = re.fullmatch(r"(T\d+)R(\d+)", rid)
        if not m:
            return False, "행 위치가 형식에 맞지 않음", None
        t = self.rep.table(m.group(1))
        r = int(m.group(2))
        if t is None or r >= len(t.grid):
            return False, "없는 표나 행", None
        if not (is_consolidated_statement(t) or (is_summary(t) and "별도" not in t.caption and "연결" in (t.caption + t.path))):
            return False, "연결재무제표 표가 아님", None
        if _ws(self.lines.get(rid, "")) != q and q not in self.lines.get(rid, ""):
            return False, "인용이 가리킨 행에 없음", None
        if not label_ok(account, t.row_label(r), strict):
            return False, "행 이름이 계정과 다름", None
        cols = period_columns(t)
        if period not in cols:
            return False, "표 머리글에서 기간 열을 못 찾음", None
        cell = parse_number(t.grid[r][cols[period]])
        if cell is None or cell != _dec(item.get("value", "")):
            return False, f"{period} 열의 숫자가 다름", None
        if not t.unit:
            return False, "표 단위를 못 찾음", None
        return True, "", cell * t.unit


def correct(rep: Report, account: str, period: str, won: Decimal | None) -> bool:
    if won is None or account not in rep.gold:
        return False
    # 반올림 허용폭은 표 단위 하나. 단위를 모르면 백만원으로 본다(가장 흔함).
    cells = rep.gold_cells.get(account, {})
    unit = 1_000_000
    if period in cells:
        t = rep.table(cells[period][0])
        unit = t.unit or unit
    return same_amount(won / unit, unit, Decimal(rep.gold[account][period])) if unit else False
