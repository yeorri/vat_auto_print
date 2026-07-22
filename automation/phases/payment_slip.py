"""납부서 출력 — 신고/납부 > 부가가치세 > 신고내역 조회(접수증·납부서).

흐름 (2026-07-22 CDP 라이브 정찰로 셀렉터 확인):
    메뉴 진입 → '신고내역 조회(접수증·납부서)' 링크 → 큰 모달(UTERNAAZ0Z31)
    → (조회기간 프리셋) → 사업자등록번호 입력(⚠ type=password 칸) → [조회]
    → '조회가 완료되었습니다' 알림(WebSquare 레이어 info*_wframe, 네이티브 alert 아님)
      → btn_confirm 클릭
    → 그리드(ttirnam101DVOListDes) 최신 행(열9=접수일시)의 열13 납부서 [보기] BUTTON
    → Report 뷰어 팝업(clipreport.do, 창 재사용됨) → 기존 인쇄→PDF 파이프라인

사업자번호 오류는 같은 알림 레이어에 "…확인하세요" 메시지 — 해당 업체만 건너뜀.
알림 확인 클릭은 반드시 해당 레이어 안의 btn_confirm으로 — 화면의 다른 '확인'
버튼(trigger6, 조회건수)을 누르면 재조회가 일어난다(정찰에서 실확인).

파일명: Inputs.slip_template의 {업체명}/{납부기한} 치환 (util.render_slip_name).
"""
from __future__ import annotations

import asyncio

from .. import hometax as H
from ..util import render_slip_name
from .base import Inputs, PhaseResult

KEY = "payment_slip"
LABEL = "납부서 출력"

# 신고/납부 > 세금신고 > 부가가치세 (사용자 제공 URL)
URL = ("https://hometax.go.kr/websquare/websquare.html"
       "?w2xPath=/ui/pp/index_pp.xml&tmIdx=04&tm2lIdx=0405000000"
       "&tm3lIdx=0405010000")

LINK_TEXT = "신고내역 조회(접수증·납부서)"

# ── 모달 셀렉터 (2026-07-22 CDP 정찰 확인) ──
PRE = "#mf_txppWframe_UTERNAAZ0Z31_wframe_"
SEL_BIZNO = f"{PRE}input_txprRgtNo_UTERNAAZ31"      # ⚠ type=password
BTN_SEARCH = (f"{PRE}trigger70_UTERNAAZ31", "조회")
PERIOD_BTN = {  # 신고일자 조회기간 프리셋
    "당일": "btnSchDay", "1주": "btnSchWeek", "1개월": "btnSch1Month",
    "3개월": "btnSch3Month", "6개월": "btnSch6Month", "1년": "btnSchYear",
}
GRID = "mf_txppWframe_UTERNAAZ0Z31_wframe_ttirnam101DVOListDes"
COL_RECEIVED = 9    # 접수일시 "2026-07-22 19:32:23"
COL_SLIP = 13       # 납부서 [보기] BUTTON (12는 접수증)

DONE_TEXT = "조회가 완료"
BIZNO_ERR_TEXT = "확인하세요"    # "사업자등록번호/주민등록번호을(를) 확인하세요."


async def _click_by_text(page, text: str) -> bool:
    """보이는 요소 중 자기 텍스트가 text를 포함하는 것을 찾아 JS 클릭."""
    try:
        return bool(await page.evaluate(
            """(txt) => {
                const els = [...document.querySelectorAll('a, button, span, div, li')];
                for (const el of els) {
                    if (!el.offsetParent) continue;
                    const own = [...el.childNodes].filter(c => c.nodeType === 3)
                        .map(c => c.textContent).join('').trim();
                    if (own.includes(txt)) {
                        (el.closest('a,button') || el).dispatchEvent(
                            new MouseEvent('click', {bubbles: true}));
                        return true;
                    }
                }
                return false;
            }""", text))
    except Exception:
        return False


async def _read_and_confirm_notice(page) -> str:
    """WebSquare 알림 레이어(info*_wframe) 감지 — 메시지 반환 + 그 레이어의
    btn_confirm 클릭. 없으면 ''. (다른 '확인' 버튼은 절대 누르지 않는다.)"""
    try:
        return await page.evaluate(
            """() => {
                for (const root of document.querySelectorAll(
                        "div[id^='mf_txppWframe_UTERNAAZ0Z31_wframe_info']")) {
                    if (!root.id.endsWith('_wframe') || !root.offsetParent) continue;
                    const msg = (root.innerText || '').replace(/\\s+/g, ' ').trim();
                    const btn = root.querySelector("input[id$='btn_confirm']");
                    if (btn) btn.dispatchEvent(
                        new MouseEvent('click', {bubbles: true}));
                    return msg.slice(0, 120);
                }
                return '';
            }""")
    except Exception:
        return ""


async def _wait_query_notice(page, timeout_sec: float = 25) -> tuple:
    """조회 후 알림 레이어 감시 — ('done'|'bizno'|'timeout', 메시지)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        msg = await _read_and_confirm_notice(page)
        if msg:
            if BIZNO_ERR_TEXT in msg and "등록번호" in msg:
                return "bizno", msg
            return "done", msg   # '조회가 완료' 외 알림도 건수로 재판정
        await asyncio.sleep(0.4)
    return "timeout", ""


async def _scan_rows(page) -> list:
    """그리드 행 스캔 — [{row, received, hasSlip}] (접수일시 텍스트 기준)."""
    try:
        return await page.evaluate(
            """([grid, colR, colS]) => {
                const out = [];
                for (let i = 0; i < 200; i++) {
                    const cr = document.getElementById(grid + '_cell_' + i + '_' + colR);
                    if (!cr) break;
                    if (!cr.offsetParent) continue;
                    const cs = document.getElementById(grid + '_cell_' + i + '_' + colS);
                    out.push({row: i,
                              received: cr.innerText.trim(),
                              hasSlip: !!(cs && cs.querySelector('button'))});
                }
                return out;
            }""", [GRID, COL_RECEIVED, COL_SLIP])
    except Exception:
        return []


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    bizno = client.get("bizno", "")
    if len(bizno) not in (10, 13):
        res.reason = "사업자(주민)등록번호 형식 오류"
        return res

    # ── ① 메뉴 진입 → 신고내역 조회 모달 열기 ──
    page = await H.goto_url(ctx, URL, log=log)
    if not await _click_by_text(page, LINK_TEXT):
        res.reason = f"'{LINK_TEXT}' 링크를 찾지 못함"
        return res
    # 모달 로딩 대기 — 사업자번호 입력칸이 보일 때까지 (최대 15초)
    try:
        await page.locator(SEL_BIZNO).wait_for(state="visible", timeout=15000)
    except Exception:
        res.reason = "신고내역 모달이 열리지 않음 (입력칸 미표시)"
        return res

    # ── ② 조회기간 프리셋 + 사업자번호 입력 → 조회 ──
    if inp.slip_period and inp.slip_period != "1개월":   # 1개월 = 홈택스 기본값
        btn = PERIOD_BTN.get(inp.slip_period)
        if btn and await H.js_click(page, f"{PRE}{btn}_UTERNAAZ31"):
            log(f"    조회기간: {inp.slip_period}")
            await asyncio.sleep(0.3)
        else:
            log(f"    [!] 조회기간 '{inp.slip_period}' 버튼 클릭 실패 — 기본값으로 진행")
    if not await H.js_fill(page, SEL_BIZNO, bizno):
        res.reason = "사업자번호 입력 실패"
        return res
    if not await H.click_button(page, *BTN_SEARCH, log):
        res.reason = "조회 버튼 클릭 실패"
        return res

    state, msg = await _wait_query_notice(page)
    if state == "bizno":
        log(f"    홈택스: {msg[:60]} — 이 업체는 건너뜁니다")
        res.reason = "사업자등록번호 오류 — 홈택스 알림, 건너뜀"
        return res
    if state == "timeout":
        res.reason = "조회 완료 알림이 안 옴(25초) — 화면 확인 필요"
        return res
    await asyncio.sleep(1.0)   # 알림 닫힘 + 그리드 렌더 여유

    # ── ③ 최신 1건의 납부서 [보기] → Report 뷰어 → PDF 저장 ──
    rows = await _scan_rows(page)
    if not rows:
        res.reason = "조회 결과 없음(신고내역 0건) — 조회기간·신고 여부 확인"
        return res
    latest = max(rows, key=lambda r: r.get("received", ""))
    log(f"    신고내역 {len(rows)}건 — 최신 1건(접수 {latest.get('received', '?')}) "
        "납부서 출력")
    if not latest.get("hasSlip"):
        log("    납부서 [보기] 버튼 없음 — 납부할 세액 없는 신고(환급 등)로 보임")
        res.ok = True
        res.reason = "납부서 없음 — 출력 생략"
        return res

    fname = render_slip_name(inp.slip_template, client.get("name", ""),
                             inp.due_date, inp.due_format)
    out = H.prepare_target(
        H.client_dir(inp, client) / f"{H.pdf_save.sanitize_filename(fname)}.pdf", log)
    slip_btn = f"#{GRID}_cell_{latest['row']}_{COL_SLIP} button"
    ok, err = await H.print_via_button(ctx, page, slip_btn, "보기", out, inp, log=log)
    if ok:
        res.outputs.append(str(out))
    res.ok = ok
    res.reason = err
    return res
