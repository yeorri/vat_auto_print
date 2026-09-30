"""Phase 공통 타입 — 입력 묶음과 결과.

각 phase는 독립 모듈(KEY/LABEL/run)로 개발하고, 동일한 Inputs/PhaseResult를 공유한다.

phase 인터페이스:
    KEY: str            # 레지스트리/GUI 식별자
    LABEL: str          # 표시 이름
    async run(ctx, client, inp, emit, dialogs, stop_check=None) -> PhaseResult
        client: {"name": 업체명, "bizno": 사업자번호 10자리}
        dialogs: 세션의 JS alert/confirm 메시지 누적 리스트 —
                 조회 후 새로 추가된 메시지로 '사업자등록번호 오류' 등 분기 검출
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Inputs:
    """실행 전에 GUI에서 한 번에 받아두는 입력. phase가 필요한 것만 골라 쓴다."""
    year: str = "2026"              # 과세기간 연도
    term: str = "1"                 # 과세기간 기수: "1" | "2"
    season: str = "확정"             # 신고시즌: "확정"(7월/1월) | "예정"(4월/10월)
    report_type: str = "예정+확정"   # (예비) 예정신고 여부가 없는 업체용 폴백
    excel_summary: bool = True      # ④ 판매대행 엑셀 후가공 — 상호별 정리본(Sheet1)+원본(Sheet2)
    output_dir: str = ""            # PDF/엑셀 저장 경로 (업체별 하위 폴더 자동 생성)
    output_mode: str = "print"      # "print"(기본 프린터 출력, 디폴트) | "pdf"(저장)
    # ── 납부서 출력 모드 전용 (payment_slip) ──
    due_date: str = ""              # 납부기한 (예: "2026-07-27") — GUI에서 한 번 입력
    due_format: str = "YY.MM.DD"    # 파일명 {납부기한} 토큰 형식 (YYYY/YY/MM/DD)
    slip_template: str = "[납부서]부가가치세_{업체명}_{납부기한}"  # 파일명 템플릿
    slip_period: str = "1개월"      # 신고내역 조회기간 프리셋 (1주|1개월|3개월|6개월)


def effective_report_type(client: dict, inp: Inputs) -> str:
    """업체별 신고구분 — 신고시즌 + 명부의 '예정신고 여부'로 결정.

    예정신고기간: 예정신고 대상(True) 업체를 '예정'으로 조회
                  (대상 아닌 업체는 pipeline이 아예 건너뜀).
    확정신고기간: 예정신고 한 업체(True) → '확정'만 (예정분은 이미 신고됨),
                  안 한 업체(False) → '예정+확정' (확정신고가 6개월 전체 커버).
    """
    if inp.season == "예정":
        return "예정"
    y = client.get("yeojung")
    if y is True:
        return "확정"
    if y is False:
        return "예정+확정"
    return inp.report_type


@dataclass
class PhaseResult:
    key: str
    label: str
    client_name: str = ""
    ok: bool = False
    outputs: list[str] = field(default_factory=list)   # 저장된 PDF/엑셀 경로
    reason: str = ""
    fatal: bool = False    # True면 이 업체의 남은 phase 전부 건너뜀 (예: 사업자번호 오류)
    skipped: bool = False  # fatal로 인해 실행 안 하고 건너뛴 항목
