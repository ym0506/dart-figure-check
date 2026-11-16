"""표본 고르기와 내려받기(opendart-py 사용).

표본 규칙(고르는 사람의 판단이 끼지 않게 미리 정함):
  2026년 3월에 접수된 유가증권시장 사업보고서(2025 회계연도, 정정 제외)를 접수번호 순으로 보면서
  공식 연결재무제표 데이터(fnlttSinglAcntAll, CFS)에 대상 계정 6개가 모두 있는 회사를 앞에서부터 N곳.
  금융회사처럼 매출액 계정이 없는 회사는 이 규칙으로 자연히 빠진다.

내려받은 원문과 데이터는 data/raw/ 에만 두고 git에 넣지 않는다.
키는 ~/.config/opendart/.env 의 DART_API_KEY 한 줄에서 읽고 출력하지 않는다.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import zipfile
from pathlib import Path

from opendart import DartClient, DisclosureSearchParams

from .gold import TARGETS, pick_gold

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


def load_key(name: str, path: Path) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"{name}가 없습니다({path}).")


def dart_key() -> str:
    return load_key("DART_API_KEY", Path.home() / ".config/opendart/.env")


async def list_annual_reports(dart: DartClient) -> list:
    out = []
    page = 1
    while True:
        items, total = await dart.search_disclosures(DisclosureSearchParams(
            bgn_de="20260301", end_de="20260331", corp_cls="Y", pblntf_detail_ty="A001",
            last_reprt_at="Y", sort="date", sort_mth="asc", page_no=page, page_count=100))
        out.extend(items)
        if page * 100 >= total or not items:
            break
        page += 1
    # 정정보고서와 2025 회계연도가 아닌 것은 뺀다
    out = [d for d in out if "정정" not in d.report_nm and "(2025.12)" in d.report_nm]
    out.sort(key=lambda d: d.rcept_no)
    return out


async def main(n: int = 10) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    async with DartClient(api_key=dart_key()) as dart:
        reports = await list_annual_reports(dart)
        print(f"3월 유가증권 사업보고서(2025.12, 정정 제외) {len(reports)}건")
        chosen, skipped = [], []
        for d in reports:
            if len(chosen) >= n:
                break
            fs = await dart.get_full_statements(d.corp_code, "2025", "11011", fs_div="CFS")
            rows = fs.get("list", [])
            gold = pick_gold(rows)
            if len(gold) < len(TARGETS):
                skipped.append({"corp": d.corp_name, "rcept_no": d.rcept_no,
                                "missing": sorted(set(TARGETS) - set(gold))})
                continue
            # 원문 zip은 여러 파일(본문, 감사보고서 등)이라 opendart의 get_document(전부 이어 붙임) 대신
            # 같은 클라이언트의 요청 함수로 zip을 그대로 받아 파일별로 저장한다.
            raw = await dart._request("document.xml", {"rcept_no": d.rcept_no})
            content = raw.get("content", b"")
            try:
                zf = zipfile.ZipFile(io.BytesIO(content))
            except zipfile.BadZipFile:
                print(f"  {d.corp_name}: 원문이 zip이 아님(앞부분: {raw.get('text', '')[:120]!r}) → 건너뜀")
                skipped.append({"corp": d.corp_name, "rcept_no": d.rcept_no, "missing": ["document.xml"]})
                continue
            folder = RAW / d.rcept_no
            folder.mkdir(exist_ok=True)
            for name in zf.namelist():
                (folder / name).write_bytes(zf.read(name))
            (folder / "gold_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            meta = {"corp_name": d.corp_name, "corp_code": d.corp_code, "stock_code": d.stock_code,
                    "rcept_no": d.rcept_no, "rcept_dt": d.rcept_dt, "report_nm": d.report_nm,
                    "files": zf.namelist()}
            (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            chosen.append(meta)
            print(f"  고름 {len(chosen)}: {d.corp_name} {d.rcept_no} 파일 {len(zf.namelist())}개")
        (RAW / "sample.json").write_text(json.dumps({"chosen": chosen, "skipped": skipped,
                                                     "rule": __doc__.split("표본 규칙")[1].split("내려받은")[0].strip()},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"고른 회사 {len(chosen)}곳, 규칙에 따라 건너뛴 회사 {len(skipped)}곳")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
