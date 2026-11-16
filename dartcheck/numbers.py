"""공시 표의 숫자와 단위 읽기.

- 천 단위 쉼표, 소수점을 읽는다.
- 음수 표기 세 가지를 모두 음수로 읽는다: 앞의 '-', 괄호 '(1,234)', 세모 '△1,234'.
- 단위 문구 '(단위 : 백만원)' 같은 것을 원 단위 배수로 바꾼다.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

UNIT_FACTOR = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "십억원": 1_000_000_000, "억원": 100_000_000}

# '단위 : 백만원', '(단위:천원)', '단위: 원' 등. 'USD' 같은 외화는 여기서 다루지 않는다.
UNIT_RE = re.compile(r"단\s*위\s*[:：]?\s*(?:KRW\s*)?(백\s*만\s*원|천\s*원|십\s*억\s*원|억\s*원|원)")

# 숫자 하나: 부호 표시(-, △, ▲ 아님), 괄호, 쉼표, 소수
NUM_RE = re.compile(
    r"(?P<neg>[-−－△])?\s*(?P<open>\()?\s*(?P<body>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<close>\))?"
)


def unit_factor(text: str) -> int | None:
    """글 안의 마지막 단위 문구를 원 단위 배수로. 없으면 None."""
    found = None
    for m in UNIT_RE.finditer(text):
        found = re.sub(r"\s+", "", m.group(1))
    return UNIT_FACTOR.get(found) if found else None


def parse_number(token: str) -> Decimal | None:
    """칸 하나의 글자를 숫자로. 숫자가 정확히 하나일 때만 읽고, 아니면 None."""
    t = token.strip()
    if t in ("", "-", "−", "－", "―", "—"):
        return None
    matches = list(NUM_RE.finditer(t))
    if len(matches) != 1:
        return None
    m = matches[0]
    # 숫자 앞뒤에 다른 글자가 남으면(예: '주석 5', '제57기') 금액으로 보지 않는다
    rest = (t[: m.start()] + t[m.end():]).strip()
    if rest:
        return None
    try:
        v = Decimal(m.group("body").replace(",", ""))
    except InvalidOperation:
        return None
    if m.group("neg") or (m.group("open") and m.group("close")):
        v = -v
    return v


def numbers_in(text: str) -> list[Decimal]:
    """글 안의 모든 숫자(부호 포함). 기수 표기('제57기')나 연도도 숫자로 나오니 쓰는 쪽에서 거른다."""
    out = []
    for m in NUM_RE.finditer(text):
        try:
            v = Decimal(m.group("body").replace(",", ""))
        except InvalidOperation:
            continue
        if m.group("neg") or (m.group("open") and m.group("close")):
            v = -v
        out.append(v)
    return out


def numbers_unsigned(text: str) -> list[Decimal]:
    """부호를 무시한 숫자들. '부호 무시 대조'가 무엇을 놓치는지 비교하려고 둔다."""
    return [abs(v) for v in numbers_in(text)]


def same_amount(doc_value: Decimal, factor: int, gold_won: Decimal) -> bool:
    """표 값(표 단위)과 공식 데이터(원)가 반올림 범위 안에서 같은지. 표 단위 1만큼 차이를 허용한다."""
    return abs(doc_value * factor - gold_won) <= factor
