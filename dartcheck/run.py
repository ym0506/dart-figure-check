"""전체 실험 실행. 표본은 DARTCHECK_SAMPLE(기본 sample=개발용 10곳, holdout=그다음 20곳)로 고른다.

  .venv/bin/python -m dartcheck.fetch 10 / 20 10 holdout   # 표본 내려받기(DART 키 필요)
  .venv/bin/python -m dartcheck.run parse      # 정답 칸 찾기와 전기 칸 확인 → results/parse{_표본}.json
  .venv/bin/python -m dartcheck.run inject     # 오류 주입(API 없음) → results/inject_gold{_표본}.json
  .venv/bin/python -m dartcheck.run recall     # 덩어리 나누기별 정답 행 recall@k(API 없음) → results/recall{_표본}.json
  .venv/bin/python -m dartcheck.run llm        # LLM 추출(DARTCHECK_MODES로 방식 선택) → results/llm_시각.json
  .venv/bin/python -m dartcheck.run items      # LLM이 낸 값 하나하나 채점 → results/llm_items_summary.json
"""

from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import inject
from .docs import load_report
from .gold import TARGETS
from .retrieve import fixed_chunks, recall_at_k, row_chunks
from .verify import Checker, correct

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RES = ROOT / "results"


def suffix() -> str:
    """표본별 결과 파일 꼬리. 개발용(sample)은 꼬리 없음, 그 밖에는 _이름(예: _holdout)."""
    name = os.environ.get("DARTCHECK_SAMPLE", "sample")
    return "" if name == "sample" else f"_{name}"


def reports():
    # DARTCHECK_SAMPLE=holdout 이면 첫 10곳 다음 회사들(행 이름 목록을 만든 뒤 처음 보는 회사)로 돈다
    name = os.environ.get("DARTCHECK_SAMPLE", "sample")
    sample = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    return [load_report(RAW / m["rcept_no"]) for m in sample["chosen"]]


def cmd_parse():
    """정답 칸 찾기. 당기 칸은 공식 당기 금액으로 찾으므로 일치가 당연하고,
    독립적으로 확인되는 것은 같은 행의 전기 칸(기간 열 판단)과 표 단위다."""
    from decimal import Decimal
    from .numbers import parse_number, same_amount
    out, tot = [], Counter()
    for rep in reports():
        prev_ok = 0
        for acct, cells in rep.gold_cells.items():
            if "전기" in cells:
                tid, r, c = cells["전기"]
                t = rep.table(tid)
                v = parse_number(t.grid[r][c])
                prev_ok += v is not None and same_amount(v, t.unit, Decimal(rep.gold[acct]["전기"]))
        row = {"corp": rep.corp_name, "tables": len(rep.tables), "accounts": len(rep.gold),
               "found_accounts": len(rep.gold_cells), "prev_match": prev_ok,
               "missing": [a for a in rep.gold if a not in rep.gold_cells]}
        out.append(row)
        tot["계정"] += row["accounts"]
        tot["찾은 계정"] += row["found_accounts"]
        tot["찾은 계정 중 전기도 공식 금액과 같음"] += prev_ok
        print(row)
    RES.mkdir(exist_ok=True)
    (RES / f"parse{suffix()}.json").write_text(json.dumps({"total": dict(tot), "rows": out}, ensure_ascii=False,
                                                          indent=1), encoding="utf-8")
    print(dict(tot))
    return out


def cmd_recall(ks=(1, 3, 5, 10)):
    from .docs import gold_rows, is_consolidated_statement, is_summary
    from .retrieve import BM25
    res = defaultdict(lambda: Counter())
    total = 0
    for rep in reports():
        stmt = [t for t in rep.tables if is_consolidated_statement(t) or is_summary(t)]
        methods = {"행 단위(재무에 관한 사항 전체)": row_chunks(rep.tables),
                   "고정 1,000자(재무에 관한 사항 전체)": fixed_chunks(rep.tables),
                   "행 단위(요약재무정보·연결재무제표 섹션만)": row_chunks(stmt)}
        bms = {m: BM25([c.text for c in ch]) for m, ch in methods.items()}
        for acct in rep.gold:
            gold = gold_rows(rep, acct)
            if not gold:
                continue
            total += 1
            for m, ch in methods.items():
                for k in ks:
                    res[m][k] += recall_at_k(ch, gold, acct, k, bms[m])
        print(rep.corp_name, {m: len(ch) for m, ch in methods.items()})
    summary = {m: {k: f"{c[k]}/{total}" for k in ks} for m, c in res.items()}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    RES.mkdir(exist_ok=True)
    (RES / f"recall{suffix()}.json").write_text(json.dumps({"total": total, "recall": summary,
                                                 "note": "정답 행 = 요약재무정보나 연결재무제표에서 행 이름이 맞고 당기 칸이 공식 금액과 같은 행"},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def cmd_llm(repeat: int = 1, workers: int = int(os.environ.get("DARTCHECK_WORKERS", "6"))):
    from concurrent.futures import ThreadPoolExecutor
    from .extract import client, extract_search, extract_statements, extract_whole
    cl = client()
    RES.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    modes = {
        "통째로": lambda rep: extract_whole(cl, rep),
        "연결 본표": lambda rep: extract_statements(cl, rep),
        "검색 상위 5행(전체)": lambda rep: extract_search(cl, rep, 5, False),
        "검색 상위 5행(섹션 거름)": lambda rep: extract_search(cl, rep, 5, True),
    }
    only = [x for x in os.environ.get("DARTCHECK_MODES", "").split(",") if x]
    if only:
        modes = {m: f for m, f in modes.items() if m in only}
    tasks = [(rep, m, i) for rep in reports() for m in modes for i in range(repeat)]

    def mem_free() -> int:
        import re as _re
        import subprocess
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
        m_ = _re.search(r"free percentage: (\d+)%", out)
        return int(m_.group(1)) if m_ else 100

    def one(task):
        rep, m, i = task
        free = mem_free()
        if free < int(os.environ.get("DARTCHECK_MIN_FREE", "8")):
            print("메모리 여유", free, "% 라 멈춤", flush=True)
            return {"corp": rep.corp_name, "rcept_no": rep.rcept_no, "mode": m, "rep": i,
                    "items": [], "usage": {"in": 0, "out": 0, "sec": 0, "usd": 0}, "error": f"skipped: memory {free}%"}
        try:
            items, usage = modes[m](rep)
            err = ""
        except Exception as e:  # noqa: BLE001 - 실패도 결과로 남긴다
            items, usage, err = [], {"in": 0, "out": 0, "sec": 0, "usd": 0}, f"{type(e).__name__}: {str(e)[:200]}"
        print(rep.corp_name, m, i, usage, err, len(items), flush=True)
        return {"corp": rep.corp_name, "rcept_no": rep.rcept_no, "mode": m, "rep": i,
                "items": items, "usage": usage, "error": err, "sample": os.environ.get("DARTCHECK_SAMPLE", "sample")}

    from .extract import LOCAL_MODEL, MODEL
    p = RES / f"llm_{stamp}.json"
    runs = []

    def save():
        p.write_text(json.dumps({"model": LOCAL_MODEL or MODEL, "repeat": repeat, "runs": runs},
                                ensure_ascii=False, indent=1), encoding="utf-8")

    # 실행 하나가 끝날 때마다 저장한다(중간에 멈춰도 끝난 결과는 남는다)
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, tasks):
            runs.append(r)
            save()
    print("저장:", p)


def cmd_score(path: str | None = None):
    files = sorted(RES.glob("llm_*.json"))
    p = Path(path) if path else files[-1]
    data = json.loads(p.read_text(encoding="utf-8"))
    reps = {r.rcept_no: r for r in reports()}
    agg = defaultdict(Counter)
    usage = defaultdict(list)
    accepted_correct = defaultdict(list)
    detail = []
    for run in data["runs"]:
        rep = reps[run["rcept_no"]]
        chk = Checker(rep)
        mode = run["mode"]
        usage[mode].append(run["usage"])
        got = {(x.get("account"), x.get("period")): x for x in run["items"] if x.get("found")}
        for acct in TARGETS:
            for period in ("당기", "전기"):
                a = agg[mode]
                a["칸"] += 1
                x = got.get((acct, period))
                if x is None:
                    a["못 찾음"] += 1
                    continue
                raw_ok = correct(rep, acct, period, chk.p1_value_won(x))
                a["검사 없이 맞음"] += raw_ok
                p1, why1 = chk.p1(x)
                p1_ok = p1 and correct(rep, acct, period, chk.p1_value_won(x))
                a["P1 받음"] += p1
                a["P1 받았는데 틀림"] += p1 and not p1_ok
                p2, why2, won = chk.p2(x, acct, period)
                p2_ok = p2 and correct(rep, acct, period, won)
                a["P2 받음"] += p2
                a["P2 받았는데 틀림"] += p2 and not p2_ok
                a["P2가 맞는 답을 거부"] += (not p2) and raw_ok
                if p2_ok and run["rep"] == 0:
                    accepted_correct[run["rcept_no"]].append(dict(x, account=acct, period=period))
                detail.append({"corp": rep.corp_name, "mode": mode, "rep": run["rep"], "account": acct,
                               "period": period, "value": x.get("value"), "unit": x.get("unit"),
                               "raw_ok": raw_ok, "p1": p1, "p1_why": why1, "p2": p2, "p2_why": why2})
    inj = []
    for rcept, items in accepted_correct.items():
        seen = set()
        uniq = []
        for it in items:   # 같은 칸을 두 방식이 모두 맞혔으면 한 번만
            key = (it["account"], it["period"])
            if key not in seen:
                seen.add(key)
                uniq.append(it)
        inj.extend(inject.run(reps[rcept], uniq))
    inj_sum = defaultdict(Counter)
    for r in inj:
        c = inj_sum[r["kind"]]
        c["넣은 수"] += 1
        c["P1 통과(틀린 값)"] += r["p1_wrong_pass"]
        c["P1 부호 무시 통과"] += r["p1_unsigned_wrong_pass"]
        c["P2 통과(틀린 값)"] += r["p2_wrong_pass"]
    use = {m: {"호출당 평균 입력 토큰": round(st.mean(u["in"] / max(1, u.get("calls", 1)) for u in us)),
               "회사당 입력 토큰(중앙값)": st.median(u["in"] for u in us),
               "회사당 시간 초(중앙값)": round(st.median(u["sec"] for u in us), 1),
               "회사당 비용 달러(평균)": round(st.mean(u["usd"] for u in us), 5)} for m, us in usage.items()}
    out = {"source": p.name, "scores": {m: dict(c) for m, c in agg.items()},
           "usage": use, "injection": {k: dict(c) for k, c in inj_sum.items()}}
    (RES / f"score_{p.stem.removeprefix('llm_')}.json").write_text(
        json.dumps({**out, "detail": detail, "injection_rows": inj}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


def cmd_inject():
    """API 없이: 정답 칸에서 코드로 만든 올바른 답 120개에 오류 5종을 넣고 P1·P2가 틀린 값을 몇 번 통과시키는지 센다."""
    from .parse import render_table
    unit_name = {1: "원", 1_000: "천원", 1_000_000: "백만원"}
    rows, sanity = [], Counter()
    for rep in reports():
        chk = Checker(rep)
        claims = []
        for acct, cells in rep.gold_cells.items():
            for period, (tid, r, c) in cells.items():
                t = rep.table(tid)
                x = {"account": acct, "period": period, "found": True, "row": f"{tid}R{r}",
                     "quote": render_table(t)[1 + r].partition("] ")[2], "value": t.grid[r][c],
                     "unit": unit_name.get(t.unit, "")}
                p1, _ = chk.p1(x)
                p2, why, won = chk.p2(x, acct, period)
                sanity["올바른 답"] += 1
                sanity["P1 받음"] += p1
                sanity["P2 받음"] += p2
                sanity["P2 받고 정답과 같음"] += p2 and correct(rep, acct, period, won)
                if not p2:
                    print("P2가 올바른 답을 거부:", rep.corp_name, acct, period, why)
                claims.append(x)
        rows.extend(inject.run(rep, claims))
    summ = defaultdict(Counter)
    for r in rows:
        c = summ[r["kind"]]
        c["넣은 수"] += 1
        c["P1 통과(틀린 값)"] += r["p1_wrong_pass"]
        c["P1 부호 무시 통과"] += r["p1_unsigned_wrong_pass"]
        c["P2 통과(틀린 값)"] += r["p2_wrong_pass"]
        c["P2 이름 부분 비교 시 통과"] += r["p2_loose_wrong_pass"]
    out = {"sanity": dict(sanity), "injection": {k: dict(v) for k, v in summ.items()}}
    RES.mkdir(exist_ok=True)
    (RES / f"inject_gold{suffix()}.json").write_text(json.dumps({**out, "rows": rows}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def cmd_items(*paths: str):
    """LLM이 낸 값 하나하나를 채점한다(같은 칸에 값을 여러 개 낸 경우도 모두 센다)."""
    files = [Path(x) for x in paths] or sorted(RES.glob("llm_2*.json"))[-2:]
    reps = {}
    for name in ("sample", "holdout"):
        os.environ["DARTCHECK_SAMPLE"] = name
        try:
            reps.update({r.rcept_no: r for r in reports()})
        except FileNotFoundError:
            pass
    agg = Counter()
    reasons = Counter()
    cells = Counter()
    per = []
    secs = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for run in data["runs"]:
            if run.get("error"):
                agg["오류로 빠진 회사"] += 1
                continue
            rep = reps[run["rcept_no"]]
            chk = Checker(rep)
            secs.append(run["usage"]["sec"])
            covered = set()
            for x in run["items"]:
                acct, period = x.get("account"), x.get("period")
                if not x.get("found") or acct not in rep.gold:
                    continue
                agg["낸 값"] += 1
                raw = correct(rep, acct, period, chk.p1_value_won(x))
                agg["맞는 값"] += raw
                p1, _ = chk.p1(x)
                agg["P1 받음"] += p1
                agg["P1이 받은 틀린 값"] += p1 and not raw
                p2, why, won = chk.p2(x, acct, period)
                p2ok = p2 and correct(rep, acct, period, won)
                agg["P2 받음"] += p2
                agg["P2가 받은 틀린 값"] += p2 and not p2ok
                agg["P2가 거부한 맞는 값"] += (not p2) and raw
                agg["P1은 받고 P2만 거부한 맞는 값"] += p1 and raw and not p2
                agg["P1은 받고 P2가 거른 틀린 값"] += p1 and not raw and not p2
                if not raw:
                    reasons[why or "P2 통과"] += 1
                if p2ok:
                    covered.add((acct, period))
                per.append({"corp": rep.corp_name, "account": acct, "period": period, "value": x.get("value"),
                            "raw_ok": raw, "p1": p1, "p2": p2, "p2_ok": p2ok, "why": why})
            cells["회사"] += 1
            cells["P2로 맞게 채운 칸"] += len(covered)
            cells["전체 칸"] += 2 * len(rep.gold)
    out = {"files": [f.name for f in files], "items": dict(agg), "wrong_value_reasons_by_P2": dict(reasons),
           "cells": dict(cells), "sec_median": st.median(secs) if secs else None}
    (RES / "llm_items_summary.json").write_text(json.dumps({**out, "rows": per}, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "parse"
    if cmd == "parse":
        cmd_parse()
    elif cmd == "recall":
        cmd_recall()
    elif cmd == "llm":
        cmd_llm(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    elif cmd == "items":
        cmd_items(*sys.argv[2:])
    elif cmd == "inject":
        cmd_inject()
    elif cmd == "score":
        cmd_score(sys.argv[2] if len(sys.argv) > 2 else None)
