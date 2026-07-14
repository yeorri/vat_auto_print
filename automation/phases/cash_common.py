"""⑤⑥ 현금영수증 매출/매입내역누계 공통 구현 — 두 화면의 셀렉터가 완전히 동일.

라이브 확인(2026-07-14, 사용자 스크린샷):
    조회년도 + 분기(예정신고 O → 확정분 분기만 / X → -전체-) → 사업자번호 3분할
    → [조회] → 월별 행 + 상·하반기 합계 + 총합계 표시
    → [인쇄] = Report 뷰어 팝업(clipReport) → 뷰어 인쇄 버튼 → 인쇄방식 패널 [인쇄]
    자료가 없으면 alert가 아니라 그리드에 '조회된 결과가 없습니다' 문구 → 출력 생략.
    ⚠ 조회 전에도 '총합계 0' 행이 미리 있음 → 완료감지는 스냅샷 변화 기준.
"""
from __future__ import annotations

import asyncio
import re

from .. import hometax as H
from .base import Inputs, PhaseResult, effective_report_type

SEL_YEAR = "#mf_txppWframe_selectYr"      # 옵션 라벨 "2026년"
SEL_QRT = "#mf_txppWframe_selectQrt"      # -전체- | 1분기 … 4분기


def quarter_label(term: str, rtype: str) -> str:
    """분기 선택 — 예정신고 여부에 따라 필요한 기간만 조회 (사용자 논의 반영).

    확정(예정신고 O): 확정분 분기만 (1기→2분기, 2기→4분기) —
        예정분(예: 3월)만 있는 업체는 '조회결과 없음'으로 자연스럽게 생략됨.
    예정+확정(X): '-전체-' (연간 누계, 월별 행으로 표시 — 1기엔 상반기가 전부).
    """
    if rtype == "확정":
        return "2분기" if term == "1" else "4분기"
    if rtype == "예정":
        return "1분기" if term == "1" else "3분기"
    return "-전체-"
SEL_BIZ1 = "#mf_txppWframe_txprDscmNo1"   # 앞 3자리
SEL_BIZ2 = "#mf_txppWframe_txprDscmNo2"   # 중간 2자리
SEL_BIZ3 = "#mf_txppWframe_txprDscmNo3"   # 뒤 5자리
BTN_SEARCH = ("#mf_txppWframe_trigger1", "조회")
BTN_PRINT = ("#mf_txppWframe_trigger12", "인쇄")   # 새창(Report 뷰어) 인쇄


async def run_cash(ctx, client: dict, inp: Inputs, emit, dialogs,
                   *, key: str, label: str, doc: str, url: str,
                   stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(key, label, client_name=client.get("name", ""))
    if len(client.get("bizno", "")) != 10:
        res.reason = "사업자번호 10자리가 아님(주민번호?) — 이 화면은 사업자번호 필요"
        return res
    page = await H.goto_url(ctx, url, log=log, ready=SEL_BIZ1)

    # ── ① 조회 조건 입력 (JS 우선 — 스크롤 흔들림 방지) ──
    try:
        if not await H.js_select(page, SEL_YEAR, f"{inp.year}년"):
            await page.select_option(SEL_YEAR, label=f"{inp.year}년")
        qrt = quarter_label(inp.term, effective_report_type(client, inp))
        if not await H.js_select(page, SEL_QRT, qrt):
            await page.select_option(SEL_QRT, label=qrt)
        log(f"    조회기간: {inp.year}년 {qrt} (예정신고 여부 반영)")
        b = client.get("bizno", "")
        for sel, part in ((SEL_BIZ1, b[:3]), (SEL_BIZ2, b[3:5]), (SEL_BIZ3, b[5:])):
            if not await H.js_fill(page, sel, part):
                await page.fill(sel, part)
    except Exception as e:
        res.reason = f"조회 조건 입력 실패: {str(e)[:80]}"
        return res

    # ── ② 조회 → 완료 감시 ──
    # ⚠ 이 화면은 조회 전에도 '총합계 0' 행이 미리 그려져 있어(라이브 확인),
    #    숫자 존재만 보면 결과가 뜨기 전에 0으로 오판한다 → 조회 전 스냅샷과
    #    '달라짐'을 완료 신호로 사용 (합계표에서 검증된 방식).
    rows_before = await H.rows_starting_with(page, "총합계")
    n0 = len(dialogs)
    if not await H.click_button(page, *BTN_SEARCH, log):
        res.reason = "조회 버튼 클릭 실패"
        return res

    async def loaded() -> bool:
        if await H.no_result_visible(page):
            return True
        rows_now = await H.rows_starting_with(page, "총합계")
        return rows_now != rows_before and any(re.search(r"\d", r) for r in rows_now)

    state = await H.wait_loaded_or_bizno_error(dialogs, n0, loaded, timeout_sec=20)
    if state == "bizno":
        res.fatal = True
        res.reason = "사업자등록번호 오류 — 홈택스 알림"
        return res
    if state == "timeout":
        log("    [!] 결과 변화 미감지(20초) — 현재 화면 기준으로 진행")

    if await H.no_result_visible(page):
        await asyncio.sleep(H.NO_RESULT_PAUSE)   # 유저가 화면에서 무자료 확인할 짬
        res.ok = True
        res.reason = "조회결과 없음 — 출력 생략"
        return res
    rows = await H.rows_starting_with(page, "총합계")
    if rows and H.row_total(rows[0]) == 0:
        await asyncio.sleep(H.NO_RESULT_PAUSE)
        res.ok = True
        res.reason = "총합계 0 — 출력 생략"
        return res

    # ── ③ 인쇄 (Report 뷰어 팝업 — print_via_button이 팝업 print 처리) ──
    out = None
    if inp.output_mode == "pdf":
        out = H.prepare_target(
            H.client_dir(inp, client) / f"{H.out_name(client, doc, inp)}.pdf", log)
    ok, err = await H.print_via_button(ctx, page, *BTN_PRINT, out, inp, log=log)
    res.ok = ok
    res.reason = err
    if ok and out is not None:
        res.outputs.append(str(out))
    return res
