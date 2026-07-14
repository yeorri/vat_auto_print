"""⑥ 현금영수증 매입총액 조회 — ⑤와 셀렉터 동일(2026-07-14 확인), 공통 구현 사용."""
from __future__ import annotations

from .. import hometax as H
from .base import Inputs, PhaseResult
from .cash_common import run_cash

KEY = "cash_purchase"
LABEL = "현금영수증 매입총액"
DOC = "현금영수증매입"
URL = H.menu_url("0602150000")


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    return await run_cash(ctx, client, inp, emit, dialogs,
                          key=KEY, label=LABEL, doc=DOC, url=URL,
                          stop_check=stop_check)
