"""예정고지 조회 — 통합조회(신고구분 '예정')의 기본사항·공제항목 값만 읽어 정리.

인쇄하지 않는 조회 전용 작업. 예정신고기간에 명부 O/X를 정하기 위한 참고 자료:
신고유형 / 법인예정고지대상 / 예정고지세액 등을 결과 엑셀에 모은다.
명부의 예정신고 O/X와 무관하게 체크된 업체 전부 실행 (IGNORE_SEASON).

판정 근거 (부가가치세법 제48조, 시행령 제90조, 법 제66조):
    - 개인 일반과세자, 직전 과세기간 공급가액 1억5천만원 미만 법인 → 예정고지
      (직전 과세기간 납부세액의 50%, 50만원 미만이면 고지하지 않음)
    - 그 외 법인 → 예정신고 의무
    - 간이과세자 → 1~6월 예정부과만 (2기 예정 없음)
예정고지 대상이 사업부진·조기환급으로 예정신고를 선택할지는 사람이 판단한다.
"""
from __future__ import annotations

import re

from . import vat_integrated as VI
from .base import Inputs, PhaseResult

KEY = "yejung_notice"
LABEL = "예정고지 조회"
IGNORE_SEASON = True   # 예정기간 X 업체 건너뛰기 규칙 미적용 (O/X를 정하기 위한 조회)

# 통합조회 화면 라벨 (2026-09-30 PDF 확인 — ⚠ 값 읽기는 라이브 검증 대상)
F_TYPE = "신고유형"
F_CORP_NOTICE = "법인예정고지대상"
F_SIMPLE_RATE = "간이부가율"
F_PERIOD = "과세기간"
F_NOTICE = "예정고지세액 (일반)"
F_UNREFUNDED = "예정신고미환급세액 (일반)"
F_SIMPLE_NOTICE = "예정부과세액 (간이)"
F_SIMPLE_FILED = "예정신고세액 (간이)"
FIELDS = [F_TYPE, F_CORP_NOTICE, F_SIMPLE_RATE, F_PERIOD,
          F_NOTICE, F_UNREFUNDED, F_SIMPLE_NOTICE, F_SIMPLE_FILED]


def amount(s) -> int | None:
    """'1,109,000' → 1109000. 숫자가 없으면 None."""
    d = re.sub(r"[^\d-]", "", str(s or ""))
    try:
        return int(d) if d not in ("", "-") else None
    except ValueError:
        return None


def classify(fields: dict, term: str) -> str:
    """읽은 값으로 예정기간 처리 판정 (참고용 문구)."""
    kind = (fields.get(F_TYPE) or "").strip()
    corp_flag = (fields.get(F_CORP_NOTICE) or "").strip().upper()
    is_simple = "간이" in kind or bool((fields.get(F_SIMPLE_RATE) or "").strip())
    notice = amount(fields.get(F_NOTICE)) or 0

    if is_simple:
        if term == "2":
            return "간이과세자 — 2기 예정 없음"
        simple = amount(fields.get(F_SIMPLE_NOTICE)) or 0
        return f"간이 예정부과 {simple:,}원" if simple else "간이 예정부과 없음"
    if "법인" in kind:
        if corp_flag == "N":
            return "예정신고 의무 (법인)"
        if corp_flag != "Y":
            return "법인 — 예정고지대상 여부 확인 필요"
    if notice:
        return f"예정고지 {notice:,}원"
    return "고지 없음 (50만원 미만·신규·유형전환 등)"


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    page, state, reason = await VI.query(ctx, client, inp, "예정", dialogs, log)
    if state == "fail":
        res.reason = reason
        return res
    if state == "bizno":
        res.fatal = True
        res.reason = "사업자등록번호 오류 — 홈택스: '사업자등록번호를 확인하시기 바랍니다'"
        return res
    if state == "no_auth":
        res.ok = True
        res.reason = "조회권한 없음 — 확인 불가 (홈택스에서 직접 확인)"
        return res
    if state == "empty":
        res.ok = True
        res.reason = "통합조회 결과 없음(빈 화면) — 확인 불가"
        return res

    fields = await VI.read_labeled_values(page, FIELDS)
    if not any(fields.get(f) for f in (F_TYPE, F_NOTICE, F_CORP_NOTICE)):
        res.reason = "화면 값 읽기 실패 (라벨 미발견) — 화면 구조 확인 필요"
        res.data = fields
        return res
    verdict = classify(fields, inp.term)
    log(f"    신고유형 {fields.get(F_TYPE) or '-'} / 법인예정고지대상 "
        f"{fields.get(F_CORP_NOTICE) or '-'} / 예정고지세액 {fields.get(F_NOTICE) or '-'}")
    res.ok = True
    res.reason = verdict
    res.data = {**fields, "판정": verdict}
    return res
