"""오류 주입 시험(API 호출 없음).

올바른 답(run.cmd_inject는 정답 칸에서 코드로 만든 답, run.cmd_score는 P2를 통과하고 정답과 맞은 LLM 답)에
오류를 하나씩 넣고 P1(글자와 숫자 대조)과 P2(표 칸 확인 추가)가 각각 틀린 값을 몇 번 통과시키는지 센다.

주의: 오답을 만드는 함수(period_columns, label_ok, is_consolidated_statement)와 P2가 검사에 쓰는 함수가 같다.
그래서 P2 열의 0은 설계대로 나오는 값이고, 이 시험은 실험보다 P2의 명세 시험에 가깝다.
정보가 있는 숫자는 P1이 통과시키는 수와 느슨한 이름 비교(strict=False)일 때 통과하는 수다.

1. 기간 바꿔치기: 같은 행(같은 인용)의 다른 기간 숫자를 고름. 앞선 10-K 프로젝트에서 못 거른 '같은 인용 속 다른 숫자'와 같은 종류.
2. 연결 대신 별도: 별도재무제표의 같은 이름 행과 그 숫자를 고름.
3. 같은 표의 다른 행: 가까운 행 중 계정 이름이 다른 행(예: 매출액 대신 매출원가).
4. 단위 착오: 숫자는 맞게 옮겼지만 단위를 잘못 적음. P2는 LLM이 적은 단위를 쓰지 않고 표 단위로 다시 계산하므로
   거부가 아니라 교정된다(결과 표의 0은 '틀린 값이 나가지 않음'이라는 뜻).
5. 부호 빠짐: 음수(괄호, △)를 양수로 적음. 음수 값이 있을 때만.
6. 숫자 한 자리 틀림.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal

from .docs import Report, is_consolidated_statement, is_summary, label_ok
from .numbers import parse_number
from .parse import period_columns, render_table
from .verify import Checker, correct


def _fmt(v: Decimal) -> str:
    s = f"{abs(v):,}"
    return f"({s})" if v < 0 else s


def mutations(rep: Report, item: dict) -> list[tuple[str, dict]]:
    out = []
    acct, period = item["account"], item["period"]
    m = re.fullmatch(r"(T\d+)R(\d+)", item["row"])
    t = rep.table(m.group(1))
    r = int(m.group(2))
    cols = period_columns(t)
    other = "전기" if period == "당기" else "당기"
    if other in cols:
        v = parse_number(t.grid[r][cols[other]])
        if v is not None:
            x = copy.deepcopy(item)
            x["value"] = t.grid[r][cols[other]]
            out.append(("기간 바꿔치기", x))
    # 별도재무제표(연결이 아닌 재무제표)의 같은 계정 행
    for s in rep.tables:
        if is_consolidated_statement(s) or is_summary(s) or "주석" in s.path or "재무제표" not in s.path:
            continue
        scols = period_columns(s)
        if period not in scols:
            continue
        for rr in range(s.header_rows, len(s.grid)):
            if label_ok(acct, s.row_label(rr)) and parse_number(s.grid[rr][scols[period]]) is not None:
                x = copy.deepcopy(item)
                x["row"] = f"{s.tid}R{rr}"
                x["quote"] = render_table(s)[1 + rr].partition("] ")[2]
                x["value"] = s.grid[rr][scols[period]]
                if parse_number(x["value"]) != parse_number(item["value"]):
                    out.append(("연결 대신 별도", x))
                break
        if any(k == "연결 대신 별도" for k, _ in out):
            break
    # 같은 표의 다른 행: 가까운 행 중 계정 이름이 다른 행(예: 매출액 대신 매출총이익)
    if period in cols:
        for d in (1, -1, 2, -2, 3, -3):
            rr = r + d
            if t.header_rows <= rr < len(t.grid) and not label_ok(acct, t.row_label(rr)) \
                    and parse_number(t.grid[rr][cols[period]]) is not None:
                x = copy.deepcopy(item)
                x["row"] = f"{t.tid}R{rr}"
                x["quote"] = render_table(t)[1 + rr].partition("] ")[2]
                x["value"] = t.grid[rr][cols[period]]
                out.append(("같은 표의 다른 행", x))
                break
    unit_swap = {"백만원": "천원", "천원": "백만원", "원": "천원"}
    u = re.sub(r"\s+", "", item.get("unit", ""))
    if u in unit_swap:
        x = copy.deepcopy(item)
        x["unit"] = unit_swap[u]
        out.append(("단위 착오", x))
    v = parse_number(item["value"])
    if v is not None and v < 0:
        x = copy.deepcopy(item)
        x["value"] = f"{abs(v):,}"
        out.append(("부호 빠짐", x))
    if v is not None:
        digits = item["value"]
        i = max(i for i, ch in enumerate(digits) if ch.isdigit())
        ch = digits[i]
        x = copy.deepcopy(item)
        x["value"] = digits[:i] + str((int(ch) + 1) % 10) + digits[i + 1:]
        out.append(("숫자 한 자리", x))
    return out


def run(rep: Report, accepted_correct: list[dict]) -> list[dict]:
    chk = Checker(rep)
    rows = []
    for item in accepted_correct:
        for kind, x in mutations(rep, item):
            p1_ok, _ = chk.p1(x)
            p1_unsigned_ok, _ = chk.p1(x, signed=False)
            p1_wrong_pass = p1_ok and not correct(rep, x["account"], x["period"], chk.p1_value_won(x))
            p1u_wrong_pass = p1_unsigned_ok and not correct(rep, x["account"], x["period"], chk.p1_value_won(x))
            p2_ok, why, won = chk.p2(x, x["account"], x["period"])
            p2_wrong_pass = p2_ok and not correct(rep, x["account"], x["period"], won)
            p2l_ok, _, wonl = chk.p2(x, x["account"], x["period"], strict=False)
            p2_loose_wrong_pass = p2l_ok and not correct(rep, x["account"], x["period"], wonl)
            rows.append({"corp": rep.corp_name, "kind": kind, "account": x["account"], "period": x["period"],
                         "p1_wrong_pass": p1_wrong_pass, "p1_unsigned_wrong_pass": p1u_wrong_pass,
                         "p2_wrong_pass": p2_wrong_pass, "p2_loose_wrong_pass": p2_loose_wrong_pass,
                         "p2_reason": why, "row_label": rep.table(x["row"].split("R")[0]).row_label(int(x["row"].split("R")[1]))})
    return rows
