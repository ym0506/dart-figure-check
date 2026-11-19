"""임베딩 검색과 BM25를 같은 행 덩어리, 같은 검색어로 비교한다(정답 행 recall@k).

    .venv/bin/pip install -r requirements-embed.txt
    .venv/bin/python -m dartcheck.embed                 # 개발용 10곳
    DARTCHECK_SAMPLE=holdout .venv/bin/python -m dartcheck.embed

- 덩어리 두 가지: 행 전체(row_chunks_short, 머리글 300자와 행 600자에서 자름), 행 이름 먼저(row_chunks_label, 숫자 칸 제외).
  둘째는 개발용 10곳에서 첫째의 임베딩 결과를 본 뒤 추가했다. BM25도 두 덩어리 모두로 잰다.
- 검색어: BM25 비교와 같은 고정 문구(retrieve.QUERY). 임베딩 쪽은 e5-instruct 형식의 지시문을 앞에 붙인다.
- 범위 두 가지: '재무에 관한 사항' 전체, 요약재무정보와 연결재무제표 섹션만.
- 지시문, 자르는 길이, RRF 상수는 처음 정한 값 그대로다. 평가용 20곳은 덩어리 두 가지를 정한 뒤에 한 번 돌렸다.
모델 파일은 처음 실행할 때 Hugging Face에서 내려받는다(모델마다 1~2GB). 행 임베딩은 data/emb/에 저장해 다시 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from .docs import gold_rows, is_consolidated_statement, is_summary
from .retrieve import BM25, QUERY, row_chunks_label, row_chunks_short, rrf
from .run import RES, ROOT, reports, suffix

MODELS = {
    "e5-large-instruct": "intfloat/multilingual-e5-large-instruct",
    "PwC-Embedding_expr": "SamilPwC-AXNode-GenAI/PwC-Embedding_expr",
}
INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"
KS = (1, 3, 5, 10)
TOP = 50          # RRF에 넣는 순위 길이
FORMS = {"행 전체": row_chunks_short, "행 이름 먼저": row_chunks_label}
EMB = ROOT / "data" / "emb"


def query_text(q: str) -> str:
    return f"Instruct: {INSTRUCT}\nQuery: {q}"


class Encoder:
    def __init__(self, repo: str):
        import torch
        from sentence_transformers import SentenceTransformer
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.repo = repo
        # 허브에서 바로 받기 어려우면 파일을 따로 받아 둔 폴더를 DARTCHECK_MODEL_DIR/<저장소 이름>에 두고 읽는다
        local = Path(os.environ.get("DARTCHECK_MODEL_DIR", "")) / repo.split("/")[-1]
        self.model = SentenceTransformer(str(local) if os.environ.get("DARTCHECK_MODEL_DIR") and local.is_dir() else repo,
                                         device=dev)
        self.model.max_seq_length = 512
        self.batch = int(os.environ.get("DARTCHECK_EMB_BATCH", "32"))

    def encode(self, texts: list[str]):
        return self.model.encode(texts, batch_size=self.batch, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)

    def docs(self, texts: list[str]):
        """행 임베딩. 같은 모델, 같은 글이면 저장해 둔 것을 읽는다."""
        import numpy as np
        h = hashlib.sha256(("\x1e".join(texts) + self.repo).encode("utf-8")).hexdigest()[:20]
        f = EMB / f"{h}.npy"
        if f.exists():
            return np.load(f)
        v = self.encode(texts)
        EMB.mkdir(parents=True, exist_ok=True)
        np.save(f, v)
        return v


def dense_order(qv, dv, idx: list[int], k: int) -> list[int]:
    """idx로 고른 행들만 놓고 내적이 큰 순서. 임베딩은 정규화돼 있어 내적이 코사인 유사도다."""
    import numpy as np
    sims = dv[idx] @ qv
    top = np.argsort(-sims, kind="stable")[:k]
    return [idx[i] for i in top]


def hit(order: list[int], chunks, gold: set, k: int) -> bool:
    return any(chunks[i].rows & gold for i in order[:k])


def main(names: list[str]):
    encs = {n: Encoder(MODELS[n]) for n in names}
    qvec = {n: dict(zip(QUERY, e.encode([query_text(q) for q in QUERY.values()]))) for n, e in encs.items()}
    res = defaultdict(Counter)
    misses = defaultdict(list)
    total, n_all, n_stmt = 0, 0, 0
    secs = Counter()
    for rep in reports():
        stmt_tids = {t.tid for t in rep.tables if is_consolidated_statement(t) or is_summary(t)}
        for form, make in FORMS.items():
            chunks = make(rep.tables)
            scopes = {"전체": list(range(len(chunks))),
                      "섹션만": [i for i, c in enumerate(chunks) if next(iter(c.rows))[0] in stmt_tids]}
            if form == "행 전체":
                n_all += len(scopes["전체"])
                n_stmt += len(scopes["섹션만"])
            dvec = {}
            for n, e in encs.items():
                t0 = time.time()
                dvec[n] = e.docs([c.text for c in chunks])
                secs[n] += time.time() - t0
            bm = {s: BM25([chunks[i].text for i in idx]) for s, idx in scopes.items()}
            for acct in rep.gold:
                gold = gold_rows(rep, acct)
                if not gold:
                    continue
                total += form == "행 전체"
                for s, idx in scopes.items():
                    orders = {"BM25": [idx[j] for j in bm[s].search(QUERY[acct], TOP)]}
                    for n in names:
                        orders[n] = dense_order(qvec[n][acct], dvec[n], idx, TOP)
                        orders[f"BM25+{n}(RRF)"] = rrf([orders["BM25"], orders[n]])
                    for m, order in orders.items():
                        key = f"{s} / {form} / {m}"
                        for k in KS:
                            res[key][k] += hit(order, chunks, gold, k)
                        if not hit(order, chunks, gold, 5):
                            misses[key].append(f"{rep.corp_name} {acct}")
        print(rep.corp_name, len(chunks), flush=True)
    summary = {m: {str(k): f"{c[k]}/{total}" for k in KS} for m, c in res.items()}
    out = {"total": total, "rows": {"전체": n_all, "섹션만": n_stmt}, "recall": summary,
           "models": {n: MODELS[n] for n in names}, "instruct": INSTRUCT, "max_seq_length": 512,
           "rrf": {"k": 60, "top": TOP}, "embed_sec": {n: round(v) for n, v in secs.items()},
           "missed_at_5": misses,
           "note": "행 전체 = row_chunks_short(머리글 300자, 행 600자), 행 이름 먼저 = row_chunks_label(숫자 칸 제외). "
                   "정답 행 정의는 recall.json과 같다."}
    RES.mkdir(exist_ok=True)
    (RES / f"recall_embed{suffix()}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    main(sys.argv[1:] or list(MODELS))
