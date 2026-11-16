from decimal import Decimal

from dartcheck.docs import Report, find_gold_cells, label_ok
from dartcheck.parse import build_tree, render_table, tables_with_context
from dartcheck.verify import Checker, correct

DOC = """<DOCUMENT><BODY>
<SECTION-1><TITLE>III. 재무에 관한 사항</TITLE>
<SECTION-2><TITLE>2. 연결재무제표</TITLE>
<P>2-2. 연결 손익계산서</P><P>(단위 : 백만원)</P>
<TABLE><THEAD><TR><TH>과 목</TH><TH>주석</TH><TH>제 57 기</TH><TH>제 56 기</TH></TR></THEAD>
<TBODY><TR><TD>매출액 (주30)</TD><TD>30</TD><TE>300,870</TE><TE>258,935</TE></TR>
<TR><TD>매출원가</TD><TD></TD><TE>186,562</TE><TE>180,388</TE></TR>
<TR><TD>영업이익(손실)</TD><TD></TD><TE>(1,234)</TE><TE>6,566</TE></TR></TBODY></TABLE>
</SECTION-2>
<SECTION-2><TITLE>4. 재무제표</TITLE>
<P>4-2. 손익계산서</P><P>(단위 : 백만원)</P>
<TABLE><THEAD><TR><TH>과 목</TH><TH>제 57 기</TH><TH>제 56 기</TH></TR></THEAD>
<TBODY><TR><TD>매출액</TD><TE>209,052</TE><TE>170,374</TE></TR></TBODY></TABLE>
</SECTION-2></SECTION-1></BODY></DOCUMENT>"""

GOLD = {"매출액": {"당기": Decimal("300870000000"), "전기": Decimal("258935000000")},
        "영업이익": {"당기": Decimal("-1234000000"), "전기": Decimal("6566000000")}}


def rep():
    tables = [t for t in tables_with_context(build_tree(DOC)) if "재무에 관한 사항" in t.path]
    return Report("X", "테스트", tables, GOLD, find_gold_cells(tables, GOLD))


def line(r, tid, row):
    return render_table(r.table(tid))[1 + row].partition("] ")[2]


def test_label_ok_strict_and_loose():
    assert label_ok("매출액", "매출액 (주30)")
    assert not label_ok("매출액", "매출원가")
    assert label_ok("매출액", "매출원가", strict=False)        # 처음 만든 부분 문자열 비교의 구멍
    assert not label_ok("자본총계", "부채와자본총계")
    assert label_ok("영업이익", "영업이익(손실)")


def test_gold_cells_found():
    r = rep()
    assert r.gold_cells["매출액"]["당기"] == ("T0", 1, 2)
    assert r.gold_cells["영업이익"]["전기"] == ("T0", 3, 3)


def test_p2_accepts_correct_and_rejects_wrong():
    r = rep()
    chk = Checker(r)
    ok = {"row": "T0R1", "quote": line(r, "T0", 1), "value": "300,870", "unit": "백만원"}
    good, why, won = chk.p2(ok, "매출액", "당기")
    assert good and correct(r, "매출액", "당기", won)
    # 같은 인용의 다른 기간 숫자: P1은 받고 P2는 거부
    swap = dict(ok, value="258,935")
    assert chk.p1(swap)[0] and not correct(r, "매출액", "당기", chk.p1_value_won(swap))
    assert chk.p2(swap, "매출액", "당기")[1] == "당기 열의 숫자가 다름"
    # 별도재무제표의 같은 이름 행
    sep = {"row": "T1R1", "quote": line(r, "T1", 1), "value": "209,052", "unit": "백만원"}
    assert chk.p1(sep)[0] and chk.p2(sep, "매출액", "당기")[1] == "연결재무제표 표가 아님"
    # 같은 표의 다른 행(매출원가)
    cost = {"row": "T0R2", "quote": line(r, "T0", 2), "value": "186,562", "unit": "백만원"}
    assert chk.p2(cost, "매출액", "당기")[1] == "행 이름이 계정과 다름"
    assert chk.p2(cost, "매출액", "당기", strict=False)[0]     # 느슨한 비교는 통과시켰다
    # 단위를 잘못 적어도 P2는 표 단위로 다시 계산한다(거부가 아니라 교정)
    unit = dict(ok, unit="천원")
    assert not correct(r, "매출액", "당기", chk.p1_value_won(unit))
    good, _, won = chk.p2(unit, "매출액", "당기")
    assert good and correct(r, "매출액", "당기", won)
    # LLM이 위치를 틀리게 적어도 인용과 같은 줄을 코드가 찾는다
    moved = dict(ok, row="T0R3")
    assert chk.p2(moved, "매출액", "당기")[0]


def test_p1_sign():
    r = rep()
    chk = Checker(r)
    neg = {"row": "T0R3", "quote": line(r, "T0", 3), "value": "1,234", "unit": "백만원"}
    assert not chk.p1(neg)[0]                 # 부호를 보면 거부
    assert chk.p1(neg, signed=False)[0]       # 부호를 무시하면 통과
