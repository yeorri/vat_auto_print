"""②③ 통합 — (세금)계산서 신고용 합계표 (전자세금계산서 + 전자계산서, 각 매출·매입).

어차피 세트로 무조건 조회하므로 GUI에서 하나의 작업으로 통합 (사용자 요청).
내부는 hapgye_common.run_hapgye를 세금계산서 → 계산서 순으로 두 번 실행:
첫 실행에서 수임사업자를 전환하고, 두 번째는 세션에 유지되므로 전환 생략된다.
"""
from __future__ import annotations

from .base import Inputs, PhaseResult
from .hapgye_common import run_hapgye

KEY = "hapgye_sum"
LABEL = "(세금)계산서 신고용 합계표"

KINDS = (("세금계산서", "전자세금계산서합계표"),
         ("계산서", "전자계산서합계표"))


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    fails, notes = [], []
    for kind, doc in KINDS:
        if stop_check and stop_check():
            break
        log(f"    ━ 전자{kind} ━")
        r = await run_hapgye(ctx, client, inp, emit, dialogs,
                             key=KEY, label=f"{LABEL}·{kind}", doc=doc,
                             kind=kind, stop_check=stop_check)
        res.outputs.extend(r.outputs)
        if r.fatal:
            res.fatal = True
            res.reason = r.reason
            return res
        if not r.ok:
            fails.append(f"{kind}: {r.reason}")
        elif r.reason:
            notes.append(f"{kind}: {r.reason}")

    res.ok = not fails
    res.reason = "; ".join(fails + notes)
    return res
