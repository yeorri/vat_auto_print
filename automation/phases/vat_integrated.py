"""① 부가가치세 신고자료 통합조회 서비스 (세무대리인).

흐름 (2026-07-14 실제 화면 DOM 확인, 조회까지 동작 검증됨):
    직접 URL 진입 → 년 입력 → 기 select → 신고구분 라디오 → 사업자번호 입력
    → [조회] → txtTxnrm(과세기간)이 채워지면 완료 → [인쇄하기] → 출력/PDF 저장

사업자번호가 틀리면 alert "사업자등록번호를 확인하시기 바랍니다." → fatal
(이 업체의 남은 phase 전부 건너뜀 — pipeline이 처리).
"""
from __future__ import annotations

from .. import hometax as H
from .base import Inputs, PhaseResult, effective_report_type

KEY = "integrated"
LABEL = "부가세 신고자료 통합조회"
DOC = "통합조회"
URL = H.menu_url("0602190000")

SEL_YEAR = "#mf_txppWframe_edtTxnrmY"          # 과세기간 년 (maxlen 4)
SEL_TERM = "#mf_txppWframe_selectHt"           # 1기/2기 select
SEL_RADIO = {                                   # 신고구분 라디오
    "예정": "#mf_txppWframe_radioRtnClCd_input_0",
    "확정": "#mf_txppWframe_radioRtnClCd_input_1",
    "예정+확정": "#mf_txppWframe_radioRtnClCd_input_2",
}
SEL_BIZNO = "#mf_txppWframe_inputBsno"         # 사업자등록번호 (10자리 한 칸)
BTN_SEARCH = ("#mf_txppWframe_trigger113", "조회")
BTN_PRINT = ("#mf_txppWframe_trigger167", "인쇄하기")
SEL_LOADED = "#mf_txppWframe_txtTxnrm"         # 조회 성공 시 '20260101-20260630' 형식

# 일부 업체(지점 등)는 조회 시 alert "조회권한이 없습니다."가 뜸 — 수임 문제 아님,
# 통합조회 화면만 권한이 없는 경우 (라이브 확인 2026-07-21, 다른 phase는 전부 정상).
# alert는 자동 수락돼 화면에 흔적이 없으므로 dialogs 메시지로 감지해야 함.
NO_AUTH_KEY = "조회권한"


async def _screen_empty(page) -> bool:
    """사업자 기본사항 칸(신고유형·관할서·상호)이 전부 공란인지.

    40초를 기다린 뒤라 로딩 중일 수는 없음 — 전부 공란이면 '빈 응답'으로 판정.
    ('상호'를 th 라벨로 찾는 방식은 아래 확인 로그에서 라이브 검증된 패턴.)
    """
    try:
        return await page.evaluate(
            """() => {
                const labels = ['신고유형', '관할서', '상호'];
                return labels.every(lb => {
                    const th = [...document.querySelectorAll('th')]
                        .find(t => t.innerText.trim() === lb);
                    const td = th && th.nextElementSibling;
                    return !td || td.innerText.trim() === '';
                });
            }""")
    except Exception:
        return False


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    if len(client.get("bizno", "")) != 10:
        res.reason = "사업자번호 10자리가 아님(주민번호?) — 이 화면은 사업자번호 필요"
        return res
    page = await H.goto_url(ctx, URL, log=log, ready=SEL_YEAR)

    # ── ① 조회 조건 입력 ──
    rtype = effective_report_type(client, inp)
    if rtype != inp.report_type:
        log(f"    신고구분(업체별): {rtype}")
    # 입력은 JS 우선 — Playwright fill/select는 스크롤을 유발해 화면이 흔들림
    try:
        if not await H.js_fill(page, SEL_YEAR, inp.year):
            await page.fill(SEL_YEAR, inp.year)
        if not await H.js_select(page, SEL_TERM, f"{inp.term}기"):
            await page.select_option(SEL_TERM, label=f"{inp.term}기")
        radio = SEL_RADIO.get(rtype)
        if radio and not await H.check_radio(page, radio, log):
            res.reason = "신고구분 라디오 선택 실패"
            return res
        if not await H.js_fill(page, SEL_BIZNO, client.get("bizno", "")):
            await page.fill(SEL_BIZNO, client.get("bizno", ""))
    except Exception as e:
        res.reason = f"조회 조건 입력 실패: {str(e)[:80]}"
        return res

    # ── ② 조회 → 완료/오류 감시 ──
    n0 = len(dialogs)
    if not await H.click_button(page, *BTN_SEARCH, log):
        res.reason = "조회 버튼 클릭 실패"
        return res

    async def loaded() -> bool:
        t = (await page.locator(SEL_LOADED).inner_text(timeout=1500)).strip()
        return len(t) >= 8   # '20260101-20260630'

    state = await H.wait_loaded_or_bizno_error(dialogs, n0, loaded,
                                               extra_keys={"no_auth": NO_AUTH_KEY})
    if state == "bizno":
        res.fatal = True
        res.reason = "사업자등록번호 오류 — 홈택스: '사업자등록번호를 확인하시기 바랍니다'"
        return res
    if state == "no_auth":
        log("    홈택스: '조회권한이 없습니다' — 통합조회만 권한 없는 업체(수임 문제 아님)")
        res.ok = True
        res.reason = "조회권한 없음(홈택스 알림) — 출력 생략"
        return res
    if state == "timeout":
        # 완료 신호(과세기간 칸)가 영영 안 오는 빈 응답의 예비 감지 — 주 감지는
        # 위 no_auth alert. 화면이 전 칸 공란이면 자료 없는 업체로 처리.
        if await _screen_empty(page):
            log("    조회 응답이 빈 화면 — 통합조회 자료 없는 업체로 처리")
            res.ok = True
            res.reason = "통합조회 결과 없음(빈 화면) — 출력 생략"
            return res
        res.reason = "조회 결과 로딩 시간 초과(40초)"
        return res

    # 상호 읽어 확인 로그 (라벨 '상호' 옆 칸)
    try:
        company = await page.evaluate(
            """() => {
                const th = [...document.querySelectorAll('th')]
                    .find(t => t.innerText.trim() === '상호');
                return th && th.nextElementSibling
                    ? th.nextElementSibling.innerText.trim() : '';
            }""")
        if company:
            log(f"    조회 완료 — 상호: {company}")
    except Exception:
        pass

    # ── ③ 인쇄 (print: 기본 프린터 / pdf: 업체 폴더에 저장) ──
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
