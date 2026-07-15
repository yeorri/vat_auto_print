"""⑦ 수출실적명세서 조회 (0602130000 — 2026-07-14 화면 DOM 확인).

기간 선택 (사용자 사양):
    예정신고 시즌            → 분기 라디오 + '1분기'(1기) / '3분기'(2기)
    확정신고 + 예정신고 O    → 분기 라디오 + '2분기'(1기) / '4분기'(2기)
    확정신고 + 예정신고 X    → 반기 라디오 + '상반기'(1기) / '하반기'(2기)

조회 후 '총 N건의 결과가 있습니다' 건수가 바뀌면 완료. 자료 없으면 그리드에
'조회된 결과가 없습니다' 문구(④⑤⑥과 동일) → 출력 생략.
인쇄하기는 ①과 같은 직접 인쇄형(팝업 없음, kiosk-printing이 처리).
"""
from __future__ import annotations

import asyncio
import re

from .. import hometax as H
from .base import Inputs, PhaseResult, effective_report_type

KEY = "export_sales"
LABEL = "수출실적명세서"
DOC = "수출실적명세서"
URL = H.menu_url("0602130000")

SEL_BIZNO = "#mf_txppWframe_inputBsno"
SEL_YEAR = "#mf_txppWframe_edtYear"            # 옵션 라벨 "2026"
RADIO = {
    "월": "#mf_txppWframe_shpnYmGubun_input_0",
    "분기": "#mf_txppWframe_shpnYmGubun_input_1",
    "반기": "#mf_txppWframe_shpnYmGubun_input_2",
}
SEL_QRT = "#mf_txppWframe_edtQrt"              # 1분기…4분기 (분기 라디오 선택 시 표시)
SEL_HALF = "#mf_txppWframe_edtHt"              # 상반기|하반기 (반기 라디오 선택 시 표시)
BTN_SEARCH = ("#mf_txppWframe_trigger93", "조회")
BTN_PRINT = ("#mf_txppWframe_trigger167", "인쇄하기")


def period_choice(term: str, rtype: str) -> tuple[str, str, str]:
    """(라디오 이름, 드롭다운 셀렉터, 옵션 라벨)."""
    if rtype == "예정":
        return "분기", SEL_QRT, ("1분기" if term == "1" else "3분기")
    if rtype == "확정":
        return "분기", SEL_QRT, ("2분기" if term == "1" else "4분기")
    return "반기", SEL_HALF, ("상반기" if term == "1" else "하반기")


async def _count_text(page) -> str:
    """'총 N건의 결과가 있습니다' 줄의 텍스트 ('' = 미표시) — 완료 감지용."""
    try:
        return await page.evaluate(
            """() => {
                for (const el of document.querySelectorAll('p, div, span')) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || '').trim().replace(/\\s+/g, '');
                    if (t.includes('건의결과가있습니다') && t.length < 40) return t;
                }
                return '';
            }""") or ""
    except Exception:
        return ""


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    if len(client.get("bizno", "")) != 10:
        res.reason = "사업자번호 10자리가 아님(주민번호?) — 이 화면은 사업자번호 필요"
        return res
    page = await H.goto_url(ctx, URL, log=log, ready=SEL_BIZNO)

    # ── ① 조회 조건 입력 ──
    try:
        if not await H.js_fill(page, SEL_BIZNO, client.get("bizno", "")):
            await page.fill(SEL_BIZNO, client.get("bizno", ""))
        if not await H.js_select(page, SEL_YEAR, inp.year):
            await page.select_option(SEL_YEAR, label=inp.year)
        radio_name, sel_period, label = period_choice(
            inp.term, effective_report_type(client, inp))
        if not await H.check_radio(page, RADIO[radio_name], log):
            res.reason = f"{radio_name} 라디오 선택 실패"
            return res
        await asyncio.sleep(0.5)   # 라디오에 따라 드롭다운 전환 여유
        if not await H.js_select(page, sel_period, label):
            await page.select_option(sel_period, label=label)
        log(f"    선적년월: {inp.year}년 {label} (예정신고 여부 반영)")
    except Exception as e:
        res.reason = f"조회 조건 입력 실패: {str(e)[:80]}"
        return res

    # ── ② 조회 → 완료 감시 ('총 N건' 줄 변화 또는 무자료 문구) ──
    count_before = await _count_text(page)
    n0 = len(dialogs)
    if not await H.click_button(page, *BTN_SEARCH, log):
        res.reason = "조회 버튼 클릭 실패"
        return res

    async def loaded() -> bool:
        if await H.no_result_visible(page):
            return True
        return (await _count_text(page)) != count_before

    state = await H.wait_loaded_or_bizno_error(dialogs, n0, loaded, timeout_sec=20)
    if state == "bizno":
        res.fatal = True
        res.reason = "사업자등록번호 오류 — 홈택스 알림"
        return res
    if state == "timeout":
        log("    [!] 결과 변화 미감지(20초) — 현재 화면 기준으로 진행")

    if await H.no_result_visible(page):
        await asyncio.sleep(H.NO_RESULT_PAUSE)
        res.ok = True
        res.reason = "조회결과 없음 — 출력 생략"
        return res
    cnt = await _count_text(page)
    if re.sub(r"\D", "", cnt) == "0":
        await asyncio.sleep(H.NO_RESULT_PAUSE)
        res.ok = True
        res.reason = "총 0건 — 출력 생략"
        return res
    log(f"    {cnt}")

    # ── ③ 인쇄 (직접 인쇄형 — ①과 동일) ──
    out = None
    if inp.output_mode == "pdf":
        out = H.prepare_target(
            H.client_dir(inp, client) / f"{H.out_name(client, DOC, inp)}.pdf", log)
    ok, err = await H.print_via_button(ctx, page, *BTN_PRINT, out, inp, log=log)
    res.ok = ok
    res.reason = err
    if ok and out is not None:
        res.outputs.append(str(out))
    return res
