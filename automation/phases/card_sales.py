"""④ 신용카드/판매(결제)대행 매출자료 조회 (2026-07-14 화면 DOM 확인).

산출물 2가지:
    (a) [인쇄하기] → 화면 전체 출력/PDF
    (b) '판매(결제)대행 매출자료' 표의 [엑셀 내려받기] (trigger164)
        — '월별 누계' 옵션은 화면 DOM에 없음 → 클릭 시 confirm으로 물어볼 가능성.
          dialog 핸들러가 자동 수락(=확인)하므로 첫 라이브에서 어느 쪽으로 받아지는지
          확인 필요. TODO(live): inp.excel_monthly 반영 방법 확정.

분기 범위: 1기 = 1~2분기, 2기 = 3~4분기 (예정=앞 분기만, 확정=뒤 분기만).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .. import hometax as H
from .base import Inputs, PhaseResult, effective_report_type

KEY = "card_sales"
LABEL = "신용카드/판매대행 매출"
DOC = "신용카드매출"
DOC_XLS = "판매대행매출"
URL = H.menu_url("0602060000")

SEL_BIZNO = "#mf_txppWframe_edtTxprDscmNo1"    # 사업자등록번호 10자리 한 칸
SEL_YEAR = "#mf_txppWframe_selectStlYr"        # 결제년도 (옵션 라벨 "2026")
SEL_QRT_FROM = "#mf_txppWframe_selectQrtFrom"  # 1분기…4분기
SEL_QRT_TO = "#mf_txppWframe_selectQrtTo"
BTN_SEARCH = ("#mf_txppWframe_trigger163", "조회")
BTN_PRINT = ("#mf_txppWframe_trigger167", "인쇄하기")
# 엑셀 버튼 3개 중 판매(결제)대행 표 소속 = trigger164 (섹션 제목으로 확인)
BTN_XLS_AGENCY = ("#mf_txppWframe_trigger164", "엑셀 내려받기")


def quarter_range(term: str, rtype: str) -> tuple[str, str]:
    first, second = ("1분기", "2분기") if term == "1" else ("3분기", "4분기")
    if rtype == "예정":
        return first, first
    if rtype == "확정":
        return second, second
    return first, second


async def run(ctx, client: dict, inp: Inputs, emit, dialogs, stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(KEY, LABEL, client_name=client.get("name", ""))
    if len(client.get("bizno", "")) != 10:
        res.reason = "사업자번호 10자리가 아님(주민번호?) — 이 화면은 사업자번호 필요"
        return res
    page = await H.goto_url(ctx, URL, log=log, ready=SEL_BIZNO)

    # ── ① 조회 조건 입력 (JS 우선 — 스크롤 흔들림 방지) ──
    try:
        if not await H.js_fill(page, SEL_BIZNO, client.get("bizno", "")):
            await page.fill(SEL_BIZNO, client.get("bizno", ""))
        if not await H.js_select(page, SEL_YEAR, inp.year):
            await page.select_option(SEL_YEAR, label=inp.year)
        q_from, q_to = quarter_range(inp.term, effective_report_type(client, inp))
        if not await H.js_select(page, SEL_QRT_FROM, q_from):
            await page.select_option(SEL_QRT_FROM, label=q_from)
        if not await H.js_select(page, SEL_QRT_TO, q_to):
            await page.select_option(SEL_QRT_TO, label=q_to)
        log(f"    결제년도 {inp.year}, {q_from}~{q_to}")
    except Exception as e:
        res.reason = f"조회 조건 입력 실패: {str(e)[:80]}"
        return res

    # ── ② 조회 → 완료 감시 ──
    # 완료 신호: '합계' 행이 조회 전 스냅샷과 달라지거나(자료 채워짐),
    #            '조회된 결과가 없습니다' 문구가 뜨거나(자료 없음 — 화면 문구, alert 아님)
    rows_before = await H.rows_starting_with(page, "합계")
    n0 = len(dialogs)
    if not await H.click_button(page, *BTN_SEARCH, log):
        res.reason = "조회 버튼 클릭 실패"
        return res

    async def loaded() -> bool:
        if await H.no_result_count(page):
            return True
        rows = await H.rows_starting_with(page, "합계")
        return rows != rows_before and any(re.search(r"\d", r) for r in rows)

    state = await H.wait_loaded_or_bizno_error(dialogs, n0, loaded, timeout_sec=20)
    if state == "bizno":
        res.fatal = True
        res.reason = "사업자등록번호 오류 — 홈택스 알림"
        return res

    # 자료 유무 판정 — 합계 행이 전부 0이거나 세 표 모두 '조회된 결과가 없습니다'면
    # 굳이 0짜리 보고서를 인쇄하지 않고 섹션별 로그만 남기고 종료 (사용자 요청)
    sum_rows = await H.rows_starting_with(page, "합계")
    n_empty = await H.no_result_count(page)
    total_sum = sum(H.row_total(r) for r in sum_rows)
    sections = ("매출자료구분별 합계조회", "신용카드·제로(온누리)페이 매출자료",
                "판매(결제)대행 매출자료")
    if n_empty >= 3 or (sum_rows and total_sum == 0):
        for s in sections:
            log(f"    {s}: 자료 없음")
        await asyncio.sleep(H.NO_RESULT_PAUSE)   # 유저가 화면에서 무자료 확인할 짬
        res.ok = True
        res.reason = "조회결과 없음 — 출력 생략"
        return res
    if n_empty:
        log(f"    자료 없는 항목 {n_empty}건 — 있는 자료만 처리")

    # ── ③ (a) 인쇄하기 (Report 뷰어 팝업 — print_via_button이 팝업 print 처리) ──
    out = None
    if inp.output_mode == "pdf":
        out = H.prepare_target(
            H.client_dir(inp, client) / f"{H.out_name(client, DOC, inp)}.pdf", log)
    ok, err = await H.print_via_button(ctx, page, *BTN_PRINT, out, inp, log=log)
    if ok and out is not None:
        res.outputs.append(str(out))

    # ── ④ (b) 판매(결제)대행 엑셀 내려받기 ──
    # 엑셀은 인쇄 모드여도 파일로 저장 — 인쇄 모드면 저장 폴더 바로 아래,
    # PDF 모드면 업체 폴더 안. 파일명: "업체명_판매결제대행 매출자료조회.xlsx"
    # 판매대행 표(마지막 '합계' 행)가 비어 있거나 0이면 엑셀 건너뜀.
    xls_err = ""
    agency_row = sum_rows[-1] if sum_rows else ""
    if agency_row and H.row_total(agency_row) == 0:
        log("    판매대행 자료 없음(합계 0) — 엑셀 건너뜀")
        ok_xls = True
    elif inp.output_dir:
        n1 = len(dialogs)
        from ..pdf_save import sanitize_filename
        xls_dir = H.client_dir(inp, client) if inp.output_mode == "pdf" \
            else Path(inp.output_dir)
        xls_dir.mkdir(parents=True, exist_ok=True)
        out_xls = H.prepare_target(xls_dir / sanitize_filename(
            f"{client.get('name', '')}_판매결제대행 매출자료조회.xlsx"), log)

        async def trigger_dl():
            if not await H.click_button(page, *BTN_XLS_AGENCY, log):
                raise RuntimeError("엑셀 버튼 클릭 실패")

        ok_xls, xls_err, saved_xls = await H.download_excel(page, trigger_dl,
                                                            out_xls, log=log)
        if ok_xls and inp.excel_summary:
            # 후가공: 상호별 정리본(Sheet1) + 원본(Sheet2) xlsx로 재작성 (사용자 요청)
            from ..agency_excel import make_summary
            rtype = effective_report_type(client, inp)
            title = f"{inp.term}기{rtype} - {client.get('name', '')}"
            ok_sum, err_sum, final_xls = make_summary(saved_xls, title, log=log)
            if ok_sum:
                saved_xls = final_xls
            else:
                log(f"    [!] 정리본 생성 실패({err_sum}) — 원본 엑셀 그대로 저장")
        if ok_xls:
            res.outputs.append(saved_xls)
        new_dialogs = dialogs[n1:]
        if any(H.NO_RESULT_KEY in m for m in new_dialogs):
            # 판매대행 표만 비어 있는 경우 — 엑셀 실패는 무시하고 정상 처리
            log("    판매대행 자료 없음 — 엑셀 건너뜀")
            ok_xls, xls_err = True, ""
        elif new_dialogs:
            # 월별 누계 등 confirm이 자동 수락됐을 수 있음 — 내용을 로그로 남겨 확인
            log(f"    엑셀 중 대화상자: {' | '.join(m[:60] for m in new_dialogs)}")
    else:
        log("    [i] 저장 폴더 미지정 — 판매대행 엑셀 내려받기 건너뜀")
        ok_xls = True

    res.ok = ok and ok_xls
    res.reason = "; ".join(x for x in (err, xls_err) if x)
    return res
