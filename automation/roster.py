"""업체 명부 — 엑셀/CSV 가져오기 + clients.json 저장.

명부: [{name, bizno, yeojung}]
    bizno   : 사업자번호 10자리 또는 주민등록번호 13자리 (합계표 화면은 주민번호 검색 지원)
    yeojung : 예정신고 여부 — 엑셀에 '예정' 키워드 열이 있으면 O/X 등으로 읽음.
              True(예정신고 함 → 확정만 조회) / False(안 함 → 예정+확정) / None(전역 설정 사용)
저장 위치는 app_data_dir() (개발: 프로젝트 폴더 / 배포: %LOCALAPPDATA%\\VatDataAuto).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .browser import app_data_dir
from .util import norm_regno

CLIENTS_PATH = app_data_dir() / "clients.json"

_TRUTHY = {"o", "○", "◯", "예", "y", "yes", "1", "true", "유", "완료", "함"}
_FALSY = {"x", "×", "아니오", "아니요", "n", "no", "0", "false", "무", "미", "안함"}


def _parse_yeojung(s: str):
    v = str(s or "").strip().lower()
    if not v:
        return None
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def load_clients() -> list[dict]:
    try:
        rows = json.loads(CLIENTS_PATH.read_text(encoding="utf-8"))
        return [r for r in rows if r.get("name")]
    except Exception:
        return []


def save_clients(rows: list[dict]) -> None:
    CLIENTS_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def import_table(path: str) -> list[dict]:
    """엑셀(.xlsx)/CSV에서 (업체명, 사업자등록번호[, 예정신고 여부]) 추출.

    열 제목 행(업체명·사업자등록번호·예정 등 키워드)을 찾아 해당 열만 읽는다 —
    다른 열이 섞여 있어도 무시. 제목 행이 없으면 앞 2열(업체명|번호)로 간주.
    """
    p = Path(path)
    if p.suffix.lower() == ".csv":
        grid = _read_csv(p)
    else:
        grid = _read_xlsx(p)

    name_i = biz_i = yeo_i = None
    header_row = -1
    for ri, row in enumerate(grid[:10]):   # 제목은 앞쪽 몇 행 안에 있다고 가정
        for ci, cell in enumerate(row):
            c = str(cell or "")
            if name_i is None and any(k in c for k in ("업체명", "거래처", "상호", "회사명")):
                name_i, header_row = ci, ri
            if biz_i is None and ("사업자" in c or "등록번호" in c):
                biz_i, header_row = ci, ri
            if yeo_i is None and "예정" in c:
                yeo_i = ci
        if name_i is not None and biz_i is not None:
            break
    if name_i is None or biz_i is None:    # 제목 행 없음 → 앞 2열 (업체명|번호)
        name_i, biz_i, yeo_i, header_row = 0, 1, None, -1

    out, seen = [], set()
    for row in grid[header_row + 1:]:
        def cell(i):
            return str(row[i]).strip() if (i is not None and i < len(row)
                                           and row[i] is not None) else ""
        name, bizno = cell(name_i), norm_regno(cell(biz_i))
        if not name or not bizno or bizno in seen:
            continue
        seen.add(bizno)
        out.append({"name": name, "bizno": bizno,
                    "yeojung": _parse_yeojung(cell(yeo_i))})
    return out


def _read_csv(p: Path) -> list[list]:
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(p, newline="", encoding=enc) as f:
                return [row for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    return []


def _read_xlsx(p: Path) -> list[list]:
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb.active
    grid = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    wb.close()
    return grid
