"""Phase 레지스트리 — 기본 실행 순서 (업체 1곳당 위에서부터 차례로).

각 phase 모듈은 KEY / LABEL / async run(ctx, client, inp, emit, dialogs, stop_check)
인터페이스. ②③(전자세금계산서·전자계산서 합계표)은 세트 조회라 hapgye_sum 하나로 통합.
"""
from __future__ import annotations

from . import (
    card_sales,
    cash_purchase,
    cash_sales,
    hapgye_sum,
    vat_integrated,
)

ALL_PHASES = [
    vat_integrated,   # ① 통합조회
    hapgye_sum,       # ②③ (세금)계산서 신고용 합계표 (각 매출·매입)
    card_sales,       # ④ 신용카드/판매대행 (+엑셀)
    cash_sales,       # ⑤ 현금영수증 매출총액
    cash_purchase,    # ⑥ 현금영수증 매입총액
]

PHASE_BY_KEY = {p.KEY: p for p in ALL_PHASES}
