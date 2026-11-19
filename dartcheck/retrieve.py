"""청킹 두 가지와 BM25 검색, 정답 행 recall@k.

- 행 단위: 표의 한 행을 하나의 덩어리로. 앞에 섹션 경로, 표 앞 문단, 머리글, 단위를 붙인다.
- 고정 길이: '재무에 관한 사항'의 표를 줄로 펼쳐 이은 글을 1,000자씩 200자 겹쳐 자른다.
검색어는 계정 이름과 '연결' 같은 낱말뿐이라 숫자는 쓰지 않는다. 한국어 형태소 분석기 없이 글자 2개씩 끊어(bigram) 센다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .parse import Table, render_table


def tokenize(s: str) -> list[str]:
    toks = []
    for w in re.findall(r"[가-힣]+|[A-Za-z]+", s or ""):
        if re.match(r"[가-힣]", w):
            toks.extend([w] if len(w) <= 2 else [w[i:i + 2] for i in range(len(w) - 1)])
        else:
            toks.append(w.lower())
    return toks


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.toks = [tokenize(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(len(t) for t in self.toks) / max(1, len(self.toks))
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = len(self.toks)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(t) for t in self.toks]

    def search(self, query: str, k: int) -> list[int]:
        q = tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.toks[i])
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            scores.append(s)
        return sorted(range(len(scores)), key=lambda i: -scores[i])[:k]


@dataclass
class Chunk:
    text: str
    rows: set   # 이 덩어리가 담은 (tid, row)


def row_chunks(tables: list[Table]) -> list[Chunk]:
    out = []
    for t in tables:
        lines = render_table(t)
        head = " / ".join(lines[1:1 + t.header_rows])
        for r in range(t.header_rows, len(t.grid)):
            out.append(Chunk(f"{lines[0]}\n{head}\n{lines[1 + r]}", {(t.tid, r)}))
    return out


def row_chunks_short(tables: list[Table], head_chars: int = 300, row_chars: int = 600) -> list[Chunk]:
    """행 단위 덩어리인데 머리글과 행을 정해진 글자 수에서 자른다.

    임베딩 모델은 512토큰까지만 읽는다. row_chunks는 머리글이 긴 표에서 행이 그 뒤로 밀려 잘리므로,
    임베딩 비교에서는 BM25와 임베딩 모두 이 덩어리를 쓴다.
    """
    out = []
    for t in tables:
        lines = render_table(t)
        head = " / ".join(lines[1:1 + t.header_rows])[:head_chars]
        for r in range(t.header_rows, len(t.grid)):
            out.append(Chunk(f"{lines[0]}\n{head}\n{lines[1 + r][:row_chars]}", {(t.tid, r)}))
    return out


def row_chunks_label(tables: list[Table]) -> list[Chunk]:
    """행 이름을 맨 앞에 두고 숫자 칸은 뺀 덩어리. 표 위치와 앞 문단은 뒤에 붙인다.

    row_chunks_short는 같은 표의 행들이 머리글과 위치 글을 똑같이 갖고 있어 임베딩이 행을 구분하기 어렵다.
    개발용 10곳에서 그 현상을 본 뒤 만든 표현이다.
    """
    out = []
    for t in tables:
        where = f"{t.path} / {t.caption[-60:]} / {(t.unit_text or '')[:60]}"
        for r in range(t.header_rows, len(t.grid)):
            out.append(Chunk(f"{t.row_label(r)}\n{where}", {(t.tid, r)}))
    return out


def rrf(orders: list[list[int]], k: int = 60) -> list[int]:
    """순위 목록 여러 개를 Reciprocal Rank Fusion으로 합친다. 점수가 같으면 번호가 작은 쪽이 앞."""
    score: dict[int, float] = {}
    for order in orders:
        for rank, i in enumerate(order):
            score[i] = score.get(i, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda i: (-score[i], i))


def fixed_chunks(tables: list[Table], size: int = 1000, overlap: int = 200) -> list[Chunk]:
    text, spans = [], []   # spans: (시작, 끝, tid, row)
    pos = 0
    for t in tables:
        for i, line in enumerate(render_table(t)):
            row = i - 1
            spans.append((pos, pos + len(line), t.tid, row))
            text.append(line)
            pos += len(line) + 1
    full = "\n".join(text)
    out = []
    start = 0
    while start < len(full):
        end = min(len(full), start + size)
        rows = {(tid, r) for a, b, tid, r in spans if r >= 0 and a < end and b > start}
        out.append(Chunk(full[start:end], rows))
        if end == len(full):
            break
        start = end - overlap
    return out


QUERY = {
    "매출액": "연결 손익계산서 매출액 수익",
    "영업이익": "연결 손익계산서 영업이익",
    "당기순이익": "연결 손익계산서 당기순이익",
    "자산총계": "연결 재무상태표 자산총계",
    "부채총계": "연결 재무상태표 부채총계",
    "자본총계": "연결 재무상태표 자본총계",
}


def recall_at_k(chunks: list[Chunk], gold: set, account: str, k: int, bm: "BM25 | None" = None) -> bool:
    bm = bm or BM25([c.text for c in chunks])
    idx = bm.search(QUERY[account], k)
    return any(chunks[i].rows & gold for i in idx)
