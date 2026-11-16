"""정답: DART 공식 재무 데이터(fnlttSinglAcntAll, 연결 CFS)에서 대상 계정의 당기·전기 금액(원)을 꺼낸다.

회사가 제출한 XBRL 재무제표에서 금감원이 만든 데이터라서 사람이나 AI가 적은 정답표가 아니다.
계정은 표준 계정 ID로 먼저 찾고, 회사가 자체 ID를 쓴 경우에만 계정 이름으로 찾는다.
"""

from __future__ import annotations

import re
from decimal import Decimal

# 대상 계정: 이름 → (표준 계정 ID 후보, 재무제표 구분 후보, 이름 후보)
TARGETS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "매출액": (("ifrs-full_Revenue",), ("IS", "CIS"), ("매출액", "수익(매출액)", "영업수익", "매출")),
    "영업이익": (("dart_OperatingIncomeLoss",), ("IS", "CIS"), ("영업이익", "영업이익(손실)", "영업손익")),
    "당기순이익": (("ifrs-full_ProfitLoss",), ("IS", "CIS"), ("당기순이익", "당기순이익(손실)", "연결당기순이익", "당기순손익")),
    "자산총계": (("ifrs-full_Assets",), ("BS",), ("자산총계",)),
    "부채총계": (("ifrs-full_Liabilities",), ("BS",), ("부채총계",)),
    "자본총계": (("ifrs-full_Equity",), ("BS",), ("자본총계",)),
}


def _amount(s: str | None) -> Decimal | None:
    if s is None:
        return None
    t = s.strip().replace(",", "")
    if t in ("", "-"):
        return None
    try:
        return Decimal(t)
    except Exception:  # noqa: BLE001
        return None


def norm_label(s: str) -> str:
    s = re.sub(r"\s+", "", s or "")
    s = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9]+[.)]", "", s)
    return s


def pick_gold(rows: list[dict]) -> dict[str, dict]:
    """{계정: {"당기": 원, "전기": 원, "account_id": ..., "account_nm": ..., "sj_div": ...}}"""
    out = {}
    for name, (ids, sjs, labels) in TARGETS.items():
        cand = [r for r in rows if r.get("account_id") in ids and r.get("sj_div") in sjs]
        if not cand:
            cand = [r for r in rows if r.get("sj_div") in sjs and norm_label(r.get("account_nm", "")) in
                    {norm_label(x) for x in labels}]
        # 손익은 IS와 CIS에 같은 값이 둘 다 있을 수 있어 첫 줄을 쓴다(ord 순)
        cand.sort(key=lambda r: (sjs.index(r.get("sj_div")), int(r.get("ord") or 0)))
        for r in cand:
            cur, prev = _amount(r.get("thstrm_amount")), _amount(r.get("frmtrm_amount"))
            if cur is not None and prev is not None:
                out[name] = {"당기": cur, "전기": prev, "account_id": r.get("account_id"),
                             "account_nm": r.get("account_nm"), "sj_div": r.get("sj_div"),
                             "thstrm_nm": r.get("thstrm_nm"), "frmtrm_nm": r.get("frmtrm_nm")}
                break
    return out
