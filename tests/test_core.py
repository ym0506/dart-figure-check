from decimal import Decimal

from dartcheck.numbers import numbers_in, parse_number, same_amount, unit_factor
from dartcheck.parse import build_tree, period_columns, render_table, tables_with_context

DOC = """<DOCUMENT><BODY>
<SECTION-1><TITLE ATOC="Y">III. 재무에 관한 사항</TITLE>
<SECTION-2><TITLE ATOC="Y">1. 요약재무정보</TITLE>
<P>가. 요약연결재무정보</P>
<TABLE><TBODY><TR><TD>(단위 : 백만원)</TD></TR></TBODY></TABLE>
<TABLE><THEAD><TR><TH>구 분</TH><TH>제 57 기</TH><TH>제 56 기</TH></TR></THEAD>
<TBODY><TR><TD>매출액</TD><TE>300,870,903</TE><TE>258,935,494</TE></TR>
<TR><TD>영업이익</TD><TE>(1,234)</TE><TE>△56</TE></TR></TBODY></TABLE>
</SECTION-2>
<SECTION-2><TITLE ATOC="Y">2. 연결재무제표</TITLE>
<P>연결 재무상태표</P><P>(단위 : 백만원)</P>
<TABLE><THEAD><TR><TH ROWSPAN="2">과 목</TH><TH COLSPAN="2">제 57 기</TH><TH>제 56 기</TH></TR>
<TR><TH>주석</TH><TH>금액</TH><TH>금액</TH></TR></THEAD>
<TBODY><TR><TD>자산총계</TD><TD>5</TD><TE>514,531,948</TE><TE>455,905,980</TE></TR></TBODY></TABLE>
</SECTION-2>
</SECTION-1></BODY></DOCUMENT>"""


def test_numbers():
    assert parse_number("300,870,903") == Decimal(300870903)
    assert parse_number("(1,234)") == Decimal(-1234)
    assert parse_number("△56") == Decimal(-56)
    assert parse_number("-") is None
    assert parse_number("제 57 기") is None
    assert unit_factor("(단위 : 백만원)") == 1_000_000
    assert unit_factor("단위: 천 원") == 1_000
    assert Decimal(-1234) in numbers_in("영업이익 | (1,234) | △56")
    assert same_amount(Decimal(300870903), 1_000_000, Decimal("300870903400000"))
    assert not same_amount(Decimal(300870903), 1_000, Decimal("300870903400000"))


def test_tables_units_periods():
    ts = tables_with_context(build_tree(DOC))
    assert len(ts) == 2                    # 단위만 적힌 작은 표는 표로 세지 않는다
    s, b = ts
    assert s.unit == 1_000_000 and "요약재무정보" in s.path
    assert period_columns(s) == {"당기": 1, "전기": 2}
    assert b.unit == 1_000_000 and "연결재무제표" in b.path
    assert b.header_rows == 2
    cols = period_columns(b)
    assert b.grid[1 + 1][cols["당기"]] == "514,531,948" and b.grid[2][cols["전기"]] == "455,905,980"
    assert render_table(s)[2].startswith("[T0R1] 매출액 | 300,870,903")


def test_year_headers_and_words():
    doc = """<SECTION-1><TITLE>III. 재무에 관한 사항</TITLE><SECTION-2><TITLE>2. 연결재무제표</TITLE><P>(단위 : 천원)</P>
    <TABLE><TR><TH>과목</TH><TH>2024년</TH><TH>2025년</TH></TR><TR><TD>자산총계</TD><TD>1,000</TD><TD>2,000</TD></TR></TABLE>
    <TABLE><TR><TH>과목</TH><TH>전기</TH><TH>당기</TH></TR><TR><TD>자산총계</TD><TD>1,000</TD><TD>2,000</TD></TR></TABLE>
    </SECTION-2></SECTION-1>"""
    a, b = tables_with_context(build_tree(doc))
    assert a.unit == 1_000
    assert period_columns(a) == {"당기": 2, "전기": 1}     # 연도가 큰 열이 당기
    assert period_columns(b) == {"당기": 2, "전기": 1}     # '당기', '전기' 낱말
