"""DART 공시 원문(document.xml 안의 XML)을 읽어 섹션과 표로 나눈다.

DART 원문은 dart4.xsd 형식이지만 잘못 닫힌 태그나 HTML 엔티티가 섞여 있어 표준 XML 파서가 자주 실패한다.
그래서 표준 라이브러리 HTMLParser로 관대하게 트리를 만든다(태그 이름은 소문자가 된다).

표는 칸(셀) 위치를 유지한다. 칸 하나를 T{표}R{행}C{열}로 부를 수 있어서, 숫자가 어느 행·어느 열(어느 기간)에 있었는지
코드가 다시 확인할 수 있다. 같은 행 안의 다른 숫자를 고른 오류는 글자 대조만으로는 못 잡고 이 위치 확인으로 잡는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .numbers import parse_number, unit_factor

SECTION_TAGS = ("section-1", "section-2", "section-3")
CELL_TAGS = ("td", "th", "te", "tu")
VOID = {"br", "img", "pgbrk", "hr"}


@dataclass
class Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)
    parent: "Node | None" = None


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k.lower(): (v or "") for k, v in attrs}, parent=self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k.lower(): (v or "") for k, v in attrs}, parent=self.cur))

    def handle_endtag(self, tag):
        # 가장 가까운 같은 이름의 조상까지 닫는다. 없으면 무시(잘못 닫힌 태그).
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def build_tree(text: str) -> Node:
    b = _Builder()
    b.feed(text)
    b.close()
    return b.root


def text_of(node) -> str:
    if isinstance(node, str):
        return node
    parts = []
    for c in node.children:
        if isinstance(c, str):
            parts.append(c)
        elif c.tag in ("br", "pgbrk"):
            parts.append(" ")
        else:
            parts.append(text_of(c))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def iter_nodes(node):
    yield node
    for c in node.children:
        if not isinstance(c, str):
            yield from iter_nodes(c)


def title_of(section: Node) -> str:
    for c in section.children:
        if not isinstance(c, str) and c.tag == "title":
            return text_of(c)
    return ""


@dataclass
class Table:
    tid: str            # 문서 전체에서 몇 번째 표인지: T0, T1, ...
    path: str           # 섹션 제목 경로: "III. 재무에 관한 사항 > 2. 연결재무제표"
    unit: int | None    # 원 단위 배수(백만원이면 1,000,000). 못 찾으면 None
    unit_text: str
    caption: str        # 표 바로 앞 문단(표 이름을 짐작하는 데 씀)
    grid: list[list[str]]   # 병합 칸을 풀어 직사각형으로 만든 칸 글자
    header_rows: int    # 맨 위 몇 줄이 머리글인지

    def header_of(self, col: int) -> str:
        return " ".join(dict.fromkeys(self.grid[r][col] for r in range(self.header_rows) if self.grid[r][col]))

    def row_label(self, row: int) -> str:
        for c in self.grid[row]:
            if c and parse_number(c) is None:
                return c
        return ""


def _table_grid(tbl: Node) -> tuple[list[list[str]], int]:
    rows = [n for n in iter_nodes(tbl) if n.tag == "tr"]
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}   # 아래로 병합된 칸
    head_rows = 0
    for r, tr in enumerate(rows):
        cells = [c for c in tr.children if not isinstance(c, str) and c.tag in CELL_TAGS]
        row: list[str] = []
        col = 0
        ci = 0
        is_head = bool(cells) and (all(c.tag == "th" for c in cells) or any(
            a.tag == "thead" for a in _ancestors(tr)))
        while ci < len(cells) or (r, col) in pending:
            if (r, col) in pending:
                row.append(pending.pop((r, col)))
                col += 1
                continue
            c = cells[ci]
            ci += 1
            txt = text_of(c)
            cs = _int(c.attrs.get("colspan"), 1)
            rs = _int(c.attrs.get("rowspan"), 1)
            for k in range(cs):
                row.append(txt)
                for dr in range(1, rs):
                    pending[(r + dr, col + k)] = txt
            col += cs
        grid.append(row)
        if is_head and head_rows == r:
            head_rows = r + 1
    width = max((len(x) for x in grid), default=0)
    grid = [x + [""] * (width - len(x)) for x in grid]
    if head_rows == 0 and grid:
        # 머리글 태그가 없으면 첫 행에 숫자 칸이 하나도 없을 때 머리글로 본다
        if all(parse_number(c) is None for c in grid[0]):
            head_rows = 1
    return grid, head_rows


def _ancestors(n: Node):
    p = n.parent
    while p is not None:
        yield p
        p = p.parent


def _int(v, default):
    try:
        return max(1, int(str(v).strip()))
    except (TypeError, ValueError):
        return default


def tables_with_context(root: Node) -> list[Table]:
    """문서 순서대로 표를 모은다. 단위는 표 앞(같은 섹션 안)에서 마지막으로 본 단위 문구를 쓴다."""
    out: list[Table] = []
    counter = [0]

    def walk(node: Node, path: list[str], state: dict):
        for c in node.children:
            if isinstance(c, str):
                t = c.strip()
                if t:
                    state["recent"] = (state["recent"] + " " + t)[-300:]
                continue
            if c.tag in SECTION_TAGS:
                walk(c, path + [title_of(c)], {"recent": "", "unit": None, "unit_text": ""})
                continue
            if c.tag == "title":
                # 섹션 제목은 경로로 쓰고, 표 묶음 안의 제목(예: '연결 재무상태표')은 다음 표의 앞 문단으로 쓴다
                if node.tag not in SECTION_TAGS:
                    state["recent"] = (state["recent"] + " " + text_of(c))[-300:]
                continue
            if c.tag == "table":
                grid, head = _table_grid(c)
                own = text_of(c)
                u_here = unit_factor(own) if len(own) < 120 else None
                if u_here is not None and not any(parse_number(x) is not None for row in grid for x in row):
                    # 단위만 적힌 작은 표: 다음 표의 단위로 기억만 한다
                    state["unit"], state["unit_text"] = u_here, own
                    state["recent"] = (state["recent"] + " " + own)[-300:]
                    continue
                u = unit_factor(own[:200]) or unit_factor(state["recent"]) or state["unit"]
                if unit_factor(state["recent"]):
                    state["unit"] = unit_factor(state["recent"])
                tid = f"T{counter[0]}"
                counter[0] += 1
                out.append(Table(tid, " > ".join(p for p in path if p), u,
                                 _unit_text(own[:200]) or _unit_text(state["recent"]) or state["unit_text"],
                                 state["recent"][-120:].strip(), grid, head))
                state["recent"] = ""
                continue
            walk(c, path, state)

    walk(root, [], {"recent": "", "unit": None, "unit_text": ""})
    return out


def _unit_text(s: str) -> str:
    from .numbers import UNIT_RE
    m = None
    for m in UNIT_RE.finditer(s):
        pass
    return re.sub(r"\s+", "", m.group(0)) if m else ""


# ── 기간(당기·전기) 판단 ───────────────────────────────────────────────

PERIOD_RE = re.compile(r"제\s*(\d{1,3})\s*기|(\d{4})\s*년|(당기|전기|전전기)")


def period_rank_key(header: str) -> int | None:
    """머리글 글자를 기간 순서값으로. 큰 값이 최근. 기간 표시가 없으면 None."""
    m = PERIOD_RE.search(header or "")
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    if m.group(2):
        return int(m.group(2))
    return {"당기": 3, "전기": 2, "전전기": 1}[m.group(3)]


def period_columns(t: Table) -> dict[str, int]:
    """표의 숫자 열 중 당기·전기 열 번호. 머리글의 기수·연도가 가장 큰 열이 당기, 그다음이 전기."""
    ranks: dict[int, int] = {}
    best: dict[int, int] = {}
    for col in range(len(t.grid[0]) if t.grid else 0):
        h = t.header_of(col)
        if "주석" in h:
            continue
        k = period_rank_key(h)
        n = sum(parse_number(t.grid[r][col]) is not None for r in range(t.header_rows, len(t.grid)))
        # 같은 기간이 여러 열이면(병합 머리글 아래 주석 칸 등) 숫자 칸이 가장 많은 열, 같으면 앞 열
        if k is not None and n > 0 and n > best.get(k, 0):
            ranks[k], best[k] = col, n
    order = sorted(ranks, reverse=True)
    out = {}
    if order:
        out["당기"] = ranks[order[0]]
    if len(order) > 1:
        out["전기"] = ranks[order[1]]
    return out


def render_table(t: Table) -> list[str]:
    """LLM에 보여 줄 줄들. 줄마다 [T3R5] 같은 위치 표시를 붙인다. 칸 사이는 ' | '."""
    lines = [f"[{t.tid}] 위치: {t.path} / 단위: {t.unit_text or '표시 없음'} / 앞 문단: {t.caption[-60:]}"]
    for r, row in enumerate(t.grid):
        lines.append(f"[{t.tid}R{r}] " + " | ".join(row))
    return lines
