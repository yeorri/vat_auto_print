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
COL_BIZNO = 7       # 사업자(주민)등록번호 "614-72-00086"
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
    """그리드 행 스캔 — [{row, bizno, received, hasSlip}].

    bizno는 사업자번호 열(7)의 숫자만 — 호출부에서 업체 번호와 일치 검증
    (번호 입력이 씹혀 전체 목록이 조회돼도 엉뚱한 업체를 절대 선택하지 않기 위함).
    """
    try:
        return await page.evaluate(
            """([grid, colB, colR, colS]) => {
                const out = [];
                for (let i = 0; i < 200; i++) {
                    const cr = document.getElementById(grid + '_cell_' + i + '_' + colR);
                    if (!cr) break;
                    if (!cr.offsetParent) continue;
                    const cb = document.getElementById(grid + '_cell_' + i + '_' + colB);
                    const cs = document.getElementById(grid + '_cell_' + i + '_' + colS);
                    out.push({row: i,
                              bizno: cb ? cb.innerText.replace(/\\D/g, '') : '',
                              received: cr.innerText.trim(),
                              hasSlip: !!(cs && cs.querySelector('button'))});
                }
                return out;
            }""", [GRID, COL_BIZNO, COL_RECEIVED, COL_SLIP])
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
    # 직전 업체 처리로 모달이 이미 열려 있으면 재진입 생략(사용자 요청 — 속도).
    # 잔여 그리드가 남아 있어도 ③의 사업자번호 일치 선택이 오출력을 막는다.
    page = await H.hometax_page(ctx)
    reused = False
    try:
        reused = await page.locator(SEL_BIZNO).is_visible()
    except Exception:
        reused = False
    if reused:
        log("    신고내역 모달 재사용 — 바로 이어서 조회")
        await _read_and_confirm_notice(page)   # 혹시 남은 알림 정리
    else:
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
        await asyncio.sleep(0.7)   # 모달 초기화 안정화 (직후 입력은 WebSquare가 지움)

    # ── ② 조회기간 프리셋 + 사업자번호 입력 → 조회 ──
    if inp.slip_period and inp.slip_period != "1개월":   # 1개월 = 홈택스 기본값
        btn = PERIOD_BTN.get(inp.slip_period)
        if btn and await H.js_click(page, f"{PRE}{btn}_UTERNAAZ31"):
            log(f"    조회기간: {inp.slip_period}")
            await asyncio.sleep(0.3)
        else:
            log(f"    [!] 조회기간 '{inp.slip_period}' 버튼 클릭 실패 — 기본값으로 진행")

    # 번호 입력 — ws_set_value(모델 직접 기록) 1순위, 값 검증 + 3회 재시도.
    # 라이브 사고: 모달 오픈 직후 js_fill 값이 WebSquare 재초기화로 지워져
    # 번호 없이 조회 → 수임업체 전체 목록이 떴음. 클릭도 전부 JS(스크롤 흔들림 방지).
    filled = False
    for attempt in (1, 2, 3):
        if not await H.ws_set_value(page, SEL_BIZNO, bizno):
            await H.js_fill(page, SEL_BIZNO, bizno)
        await asyncio.sleep(0.3)
        val = await page.evaluate(
            """(id) => {
                const el = document.getElementById(id);
                return el ? el.value : '';
            }""", SEL_BIZNO.lstrip("#"))
        if "".join(ch for ch in val if ch.isdigit()) == bizno:
            filled = True
            break
        log(f"    [!] 번호 입력이 지워짐(시도 {attempt}) — 다시 입력")
        await asyncio.sleep(0.5)
    if not filled:
        res.reason = "사업자번호 입력 실패(3회) — 모달 상태 확인 필요"
        return res

    if not await H.js_click(page, BTN_SEARCH[0]):
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
    # 안전장치: 사업자번호 일치 행만 — 번호 입력이 씹혀 전체 목록이 떠도
    # 엉뚱한 업체의 납부서를 절대 출력하지 않는다 (수임전환 모달 사고의 교훈)
    mine = [r for r in rows if r.get("bizno") == bizno]
    if not mine:
        res.reason = (f"조회 결과 {len(rows)}건에 해당 사업자번호 행이 없음 — "
                      "번호 입력 실패 가능, 재실행 필요")
        return res
    latest = max(mine, key=lambda r: r.get("received", ""))
    log(f"    신고내역 {len(mine)}건 — 최신 1건(접수 {latest.get('received', '?')}) "
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
    try:   # locator 클릭의 자동 스크롤로 모달이 흔들리지 않게 미리 중앙 정렬
        await page.evaluate(
            """(sel) => { const el = document.querySelector(sel);
                          if (el) el.scrollIntoView({block: 'center'}); }""",
            slip_btn)
    except Exception:
        pass
    # 인쇄 클릭이 드물게 씹혀 저장 다이얼로그가 안 뜨는 레이스(연속 실행 라이브
    # 확인) — 실패 시 [보기]부터 1회 재시도 (팝업은 print_via_button이 정리함)
    ok, err = False, ""
    for attempt in (1, 2):
        ok, err = await H.print_via_button(ctx, page, slip_btn, "보기", out, inp,
                                           log=log)
        if ok or "다이얼로그" not in (err or ""):
            break
        log(f"    [!] 인쇄 무반응(시도 {attempt}) — 납부서 보기부터 재시도")
        await asyncio.sleep(1.5)
    if ok:
        res.outputs.append(str(out))
    res.ok = ok
    res.reason = err
    return res
