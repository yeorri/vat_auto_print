"""⑤ 현금영수증 매출총액 조회 — 공통 구현(cash_common) 사용."""
from __future__ import annotations

from .. import hometax as H
from .base import Inputs, PhaseResult
from .cash_common import run_cash

KEY = "cash_sales"
LABEL = "현금영수증 매출총액"
DOC = "현금영수증매출"
URL = H.menu_url("0602070000")


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    return await run_cash(ctx, client, inp, emit, dialogs,
                          key=KEY, label=LABEL, doc=DOC, url=URL,
                          stop_check=stop_check)
