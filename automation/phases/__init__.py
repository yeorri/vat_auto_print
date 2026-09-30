"""Phase 레지스트리 — 기본 실행 순서 (업체 1곳당 위에서부터 차례로).

각 phase 모듈은 KEY / LABEL / async run(ctx, client, inp, emit, dialogs, stop_check)
인터페이스. ②③(전자세금계산서·전자계산서 합계표)은 세트 조회라 hapgye_sum 하나로 통합.
"""
from __future__ import annotations

from . import (
    card_sales,
    cash_sales,
    export_sales,
    hapgye_sum,
    payment_slip,
    vat_integrated,
)

ALL_PHASES = [
    vat_integrated,   # ① 통합조회
    hapgye_sum,       # ②③ (세금)계산서 신고용 합계표 (각 매출·매입)
    card_sales,       # ④ 신용카드/판매대행 (+엑셀)
    cash_sales,       # ⑤ 현금영수증 매출총액
    export_sales,     # ⑦ 수출실적명세서
    # ⑥ 현금영수증 매입총액(cash_purchase)은 v1.0.5에서 제거 — 사용자 불필요 확인
]

# 납부서 출력은 '자료 출력' phase 목록(ALL_PHASES = GUI 작업선택)에 넣지 않고
# 별도 모드로만 실행 — PHASE_BY_KEY에는 포함해 pipeline이 찾을 수 있게 한다.
PHASE_BY_KEY = {p.KEY: p for p in ALL_PHASES + [payment_slip]}
