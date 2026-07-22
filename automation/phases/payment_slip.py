"""납부서 출력 — 신고/납부 > 부가가치세 > 신고내역 조회(접수증·납부서).

흐름 (2026-07-22 사용자 스크린샷 기반 — ⚠ 셀렉터는 라이브 미검증, 텍스트 탐색 방식):
    메뉴 진입 → '신고내역 조회(접수증·납부서)' 클릭 → 큰 모달
    → 사업자등록번호 입력(+조회기간 프리셋) → [조회]
    → '알림: 조회가 완료되었습니다' DOM 모달 → 확인 클릭
    → 최신 1건 행의 납부서 [보기] → Report 뷰어 팝업 → PDF 저장(업체 폴더)

주의: 이 화면의 알림은 네이티브 alert가 아니라 WebSquare DOM 모달(알림/확인 버튼)
— dialogs 자동수락에 안 잡히므로 DOM에서 직접 감지·확인 클릭한다.
사업자번호 오류('…확인하세요' 알림)는 해당 업체만 건너뛰고 기록.

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
DONE_TEXT = "조회가 완료되었습니다"
BIZNO_ERR_TEXT = "확인하세요"          # "사업자등록번호/주민등록번호을(를) 확인하세요."


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


async def _tag_modal(page) -> dict:
    """신고내역 모달 안의 구성요소를 찾아 data-vap 속성으로 태깅.

    반환: {"bizno": bool, "search": bool, "periods": [...]} — 발견 여부 로그용.
    셀렉터 id를 모르는 상태라 라벨/텍스트 기반 탐색 (라이브 검증 대상).
    """
    try:
        return await page.evaluate(
            """() => {
                const vis = el => !!el.offsetParent;
                const ownText = el => [...el.childNodes]
                    .filter(c => c.nodeType === 3)
                    .map(c => c.textContent).join('').trim();
                // ① 사업자등록번호 입력칸 — 라벨 텍스트를 포함한 조상 5단계 내의
                //    보이는 text input
                let biznoInput = null;
                for (const inp of document.querySelectorAll(
                        "input[type=text], input:not([type])")) {
                    if (!vis(inp)) continue;
                    let node = inp;
                    for (let i = 0; i < 5 && node; i++) {
                        node = node.parentElement;
                        if (node && node.innerText &&
                            node.innerText.includes('사업자등록번호/') &&
                            node.innerText.length < 400) {
                            biznoInput = inp;
                            break;
                        }
                    }
                    if (biznoInput) break;
                }
                if (biznoInput) biznoInput.setAttribute('data-vap', 'slip_bizno');
                // ② 모달 루트 — 입력칸에서 위로 올라가며 팝업창 컨테이너 추정
                let root = document.body;
                if (biznoInput) {
                    let node = biznoInput;
                    while (node && node !== document.body) {
                        const cls = node.className || '';
                        const id = node.id || '';
                        if (String(cls).includes('w2window') ||
                            id.includes('wframe')) { root = node; break; }
                        node = node.parentElement;
                    }
                }
                root.setAttribute('data-vap-root', '1');
                // ③ 조회 버튼 — 모달 루트 안 텍스트/value '조회'
                let search = null;
                for (const el of root.querySelectorAll(
                        "a, button, input[type=button], input[type=submit]")) {
                    if (!vis(el)) continue;
                    const t = (el.value || ownText(el) || el.innerText || '').trim();
                    if (t === '조회') search = el;   // 마지막 것 사용
                }
                if (search) search.setAttribute('data-vap', 'slip_search');
                // ④ 조회기간 프리셋 버튼 (당일/1주/1개월/3개월/6개월/1년)
                const periods = [];
                for (const el of root.querySelectorAll(
                        "a, button, input[type=button], span, li")) {
                    if (!vis(el)) continue;
                    const t = (el.value || ownText(el) || '').trim();
                    if (['당일','1주','1개월','3개월','6개월','1년'].includes(t)) {
                        el.setAttribute('data-vap', 'slip_period_' + t);
                        periods.push(t);
                    }
                }
                return {bizno: !!biznoInput, search: !!search, periods};
            }""")
    except Exception:
        return {"bizno": False, "search": False, "periods": []}


async def _dismiss_notice(page) -> str:
    """WebSquare '알림' DOM 모달 감지 — 있으면 확인 클릭 후 메시지 반환, 없으면 ''."""
    try:
        return await page.evaluate(
            """() => {
                const vis = el => !!el.offsetParent;
                // 알림 레이어: '조회가 완료' 또는 '확인하세요' 텍스트가 보이는 노드
                for (const key of ['조회가 완료되었습니다', '확인하세요',
                                   '없습니다']) {
                    for (const el of document.querySelectorAll('div, span, p, td')) {
                        if (!vis(el)) continue;
                        const own = [...el.childNodes].filter(c => c.nodeType === 3)
                            .map(c => c.textContent).join('').trim();
                        if (!own.includes(key)) continue;
                        // 알림 컨테이너에서 '확인' 버튼을 찾아 클릭
                        let node = el;
                        for (let i = 0; i < 8 && node; i++) {
                            const btns = [...node.querySelectorAll(
                                "a, button, input[type=button]")].filter(b => {
                                    const t = (b.value || b.innerText || '').trim();
                                    return vis(b) && t === '확인';
                                });
                            if (btns.length) {
                                btns[btns.length - 1].dispatchEvent(
                                    new MouseEvent('click', {bubbles: true}));
                                return own;
                            }
                            node = node.parentElement;
                        }
                        return own;   // 확인 버튼 못 찾아도 메시지는 보고
                    }
                }
                return '';
            }""")
    except Exception:
        return ""


async def _wait_query_result(page, dialogs, n0, timeout_sec: float = 25) -> tuple:
    """조회 후 감시 — ('done'|'bizno'|'timeout', 알림메시지).

    DOM '알림' 모달(주 경로)과 네이티브 alert(dialogs, 예비) 둘 다 감시.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        msg = await _dismiss_notice(page)
        if not msg:
            for m in dialogs[n0:]:
                if DONE_TEXT in m or BIZNO_ERR_TEXT in m:
                    msg = m
                    break
        if msg:
            if BIZNO_ERR_TEXT in msg and "등록번호" in msg:
                return "bizno", msg
            if DONE_TEXT in msg:
                return "done", msg
            # 그 외 알림(예: '조회된 결과가 없습니다')도 done으로 — 건수로 재판정
            return "done", msg
        await asyncio.sleep(0.4)
    return "timeout", ""


async def _pick_latest_slip_button(page) -> dict:
    """조회 결과 그리드에서 최신 1건 행의 납부서 [보기] 버튼을 태깅.

    행마다 '보기'가 2개(접수증/납부서) — 헤더 순서상 납부서가 뒤이므로 행의
    마지막 '보기'를 납부서로 본다 (⚠ 라이브 검증 대상).
    최신 행: 행 텍스트의 접수일시(YYYY-MM-DD…)가 가장 큰 행, 없으면 첫 행.
    반환: {"count": 행 수, "picked": bool, "rowText": 선택 행 요약}
    """
    try:
        return await page.evaluate(
            """() => {
                const vis = el => !!el.offsetParent;
                const rows = [];
                const seen = new Set();
                for (const el of document.querySelectorAll(
                        "a, button, input[type=button], span")) {
                    if (!vis(el)) continue;
                    const t = (el.value || el.innerText || '').trim();
                    if (t !== '보기') continue;
                    const tr = el.closest('tr');
                    if (!tr || seen.has(tr)) continue;
                    seen.add(tr);
                    const btns = [...tr.querySelectorAll(
                        "a, button, input[type=button], span")].filter(b =>
                            vis(b) && (b.value || b.innerText || '').trim() === '보기');
                    rows.push({tr, btns, text: tr.innerText.replace(/\\s+/g, ' ')});
                }
                if (!rows.length) return {count: 0, picked: false, rowText: ''};
                const ts = txt => {
                    const m = txt.match(/20\\d{2}[-./]\\d{2}[-./]\\d{2}[^ ]*/g);
                    return m ? m[m.length - 1] : '';
                };
                rows.sort((a, b) => ts(b.text).localeCompare(ts(a.text)));
                const row = rows[0];
                const btn = row.btns[row.btns.length - 1];   // 마지막 '보기' = 납부서
                btn.setAttribute('data-vap', 'slip_view');
                return {count: rows.length, picked: true,
                        rowText: row.text.slice(0, 120),
                        btnsInRow: row.btns.length};
            }""")
    except Exception:
        return {"count": 0, "picked": False, "rowText": ""}


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
    await asyncio.sleep(2.0)   # 모달 로딩

    found = await _tag_modal(page)
    if not found.get("bizno") or not found.get("search"):
        res.reason = (f"모달 구성요소 탐색 실패 (입력칸 {found.get('bizno')}, "
                      f"조회버튼 {found.get('search')}) — 셀렉터 확인 필요")
        return res

    # ── ② 조회기간 프리셋 + 사업자번호 입력 → 조회 ──
    if inp.slip_period and inp.slip_period != "1개월":   # 1개월 = 홈택스 기본값
        try:
            await page.locator(
                f"[data-vap=slip_period_{inp.slip_period}]").first.click(timeout=3000)
            log(f"    조회기간: {inp.slip_period}")
            await asyncio.sleep(0.3)
        except Exception:
            log(f"    [!] 조회기간 '{inp.slip_period}' 버튼 클릭 실패 — 기본값으로 진행")
    try:
        ok_fill = await page.evaluate(
            """(v) => {
                const el = document.querySelector('[data-vap=slip_bizno]');
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.blur();
                return el.value === v;
            }""", bizno)
        if not ok_fill:
            res.reason = "사업자번호 입력 실패"
            return res
    except Exception as e:
        res.reason = f"사업자번호 입력 예외: {str(e)[:80]}"
        return res

    n0 = len(dialogs)
    try:
        await page.locator("[data-vap=slip_search]").first.click(timeout=5000)
    except Exception:
        res.reason = "조회 버튼 클릭 실패"
        return res

    state, msg = await _wait_query_result(page, dialogs, n0)
    if state == "bizno":
        log(f"    홈택스: {msg[:60]} — 이 업체는 건너뜁니다")
        res.reason = "사업자등록번호 오류 — 홈택스 알림, 건너뜀"
        return res
    if state == "timeout":
        res.reason = "조회 완료 알림이 안 옴(25초) — 화면 확인 필요"
        return res
    await asyncio.sleep(1.0)   # 알림 닫힘 + 그리드 렌더 여유

    # ── ③ 최신 1건의 납부서 [보기] → Report 뷰어 → PDF 저장 ──
    picked = await _pick_latest_slip_button(page)
    if not picked.get("count"):
        res.reason = "조회 결과 없음(신고내역 0건) — 조회기간·신고 여부 확인"
        return res
    if not picked.get("picked"):
        res.reason = "납부서 [보기] 버튼을 찾지 못함"
        return res
    if picked.get("btnsInRow", 0) < 2:
        log("    [!] 행에 보기 버튼이 1개뿐 — 납부서 여부 불확실 (라이브 확인 필요)")
    log(f"    신고내역 {picked['count']}건 — 최신 1건 납부서 출력: "
        f"{picked.get('rowText', '')[:60]}")

    fname = render_slip_name(inp.slip_template, client.get("name", ""),
                             inp.due_date, inp.due_format)
    out = H.prepare_target(
        H.client_dir(inp, client) / f"{H.pdf_save.sanitize_filename(fname)}.pdf", log)
    ok, err = await H.print_via_button(ctx, page, "[data-vap=slip_view]", "보기",
                                       out, inp, log=log)
    if ok:
        res.outputs.append(str(out))
    res.ok = ok
    res.reason = err
    return res
