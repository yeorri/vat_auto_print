"""②③ 부가세신고용 합계표 조회 공통 구현 (0602120000 — 2026-07-14 라이브 검증).

검증된 흐름 (모달·조회·명세서 조회까지 실제 실행 확인):
    [수임사업자전환](trigger501) 모달 → 구분 select(사업자/주민등록번호, 자릿수로 자동판단)
    → 번호 입력 → 조회 → 결과 행 라디오 → [확인](btnProcess)
    → 분기별 select(selectbox3: "1기 확정" 등) → [조회](trigger24)
    → 소계 행 숫자 채워짐 = 완료 신호
    → 전송기간 내(trigger301)/외(trigger30) 각각: 소계 합이 0이면 건너뜀,
      아니면 [명세서 조회] → [명세서 출력](trigger33, OZ viewer 새창) 인쇄.
      ('조회결과가 없습니다' alert가 오면 그것도 건너뜀 신호)

분기별 옵션은 시즌에 따라 "1기 …"/"2기 …"만 노출될 수 있음 — 라벨 못 찾으면
현재 옵션 목록을 사유에 남긴다.
"""
from __future__ import annotations

import asyncio
import re

from .. import hometax as H
from .base import Inputs, PhaseResult, effective_report_type

URL = H.menu_url("0602120000")

P = "#mf_txppWframe_"
PM = "#mf_txppWframe_UTEETZZA21_wframe_"   # 수임사업자전환 모달 프레임

SEL_KIND = {
    "세금계산서": P + "radioEtxivClsfCd_input_0",
    "계산서": P + "radioEtxivClsfCd_input_1",
}
# 매입·매출 구분 — ②③ 각각 매출→매입 순서로 조회·출력 (총 4종)
SEL_SIDE = {"매출": P + "radio2_input_0", "매입": P + "radio2_input_1"}
SIDES = ["매출", "매입"]

BTN_MODAL_OPEN = (P + "trigger501", "수임사업자전환")
M_SELECT = PM + "selectbox5"       # -전체- | 사업자등록번호 | 주민등록번호
M_NUM = PM + "txprDscmNoA"         # 등록번호 입력
M_SEARCH = PM + "trigger85"        # 모달 안 조회
M_RADIO0 = "#G_mf_txppWframe_UTEETZZA21_wframe_grdResult___radio_chk_0"  # 첫 행(존재 감지용)
M_RADIO_FMT = "#G_mf_txppWframe_UTEETZZA21_wframe_grdResult___radio_chk_{}"
M_OK = PM + "btnProcess"           # 확인
M_CLOSE = PM + "btnClose"          # 닫기

SEL_PERIOD = P + "selectbox3"      # "1기 예정 | 1기 확정 | 1기(예정+확정)"
BTN_SEARCH = (P + "trigger24", "조회")
BTN_DETAIL_IN = P + "trigger301"   # 전송기간 내 명세서 조회 (value 중복 — id로만 클릭)
BTN_DETAIL_OUT = P + "trigger30"   # 전송기간 외 명세서 조회
BTN_PRINT_DETAIL = (P + "trigger33", "명세서 출력")   # OZ viewer 새창

NO_RESULT_KEY = "없습니다"          # alert "조회결과가 없습니다."


def period_label(term: str, rtype: str) -> str:
    if rtype == "예정+확정":
        return f"{term}기(예정+확정)"
    return f"{term}기 {rtype}"


async def _select_client_via_modal(page, client: dict, dialogs: list, log) -> tuple[bool, str]:
    """수임사업자전환 모달에서 등록번호로 업체 선택. (성공, 사유)."""
    if not await H.click_button(page, *BTN_MODAL_OPEN, log):
        return False, "수임사업자전환 버튼 클릭 실패"
    try:
        await page.wait_for_selector(M_OK, state="visible", timeout=10000)
    except Exception:
        return False, "수임사업자전환 모달이 뜨지 않음"

    num = re.sub(r"\D", "", client.get("bizno", ""))
    id_type = "주민등록번호" if len(num) == 13 else "사업자등록번호"

    # ⚠ 번호 필터가 복불복으로 안 먹히는 레이스 확인(라이브): 구분 선택 후 WebSquare가
    #   입력칸을 늦게 재초기화하며 넣어둔 값을 지운다 → 입력→값검증→조회→결과검증을
    #   최대 3회 재시도. 행 선택도 순번이 아닌 '사업자번호 일치 행'만 고른다.
    async def _grid_rows() -> list:
        return await page.evaluate(
            """() => {
                const out = [];
                for (let i = 0; ; i++) {
                    const first = document.getElementById(
                        'mf_txppWframe_UTEETZZA21_wframe_grdResult_cell_' + i + '_1');
                    if (!first) break;
                    let t = '';
                    for (let j = 0; j < 8; j++) {
                        const c = document.getElementById(
                            'mf_txppWframe_UTEETZZA21_wframe_grdResult_cell_'
                            + i + '_' + j);
                        if (c) t += ' ' + (c.innerText || '');
                    }
                    out.push(t.trim());
                }
                return out;
            }""")

    target, rows = None, []
    for attempt in (1, 2, 3):
        # 구분 선택 확인 — 번호 입력칸이 비활성이면 구분부터 다시
        num_enabled = await page.evaluate(
            """() => {
                const el = document.getElementById(
                    'mf_txppWframe_UTEETZZA21_wframe_txprDscmNoA');
                return !!el && !el.disabled;
            }""")
        if not num_enabled:
            await H.js_select(page, M_SELECT, id_type)
            try:
                await page.select_option(M_SELECT, label=id_type)
            except Exception:
                pass
            await asyncio.sleep(0.7)   # 입력칸 활성화 대기

        # 번호 입력 — WebSquare 모델에 직접 기록(setValue)이 1순위(레이스 없음),
        # 안 되면 fill + blur(Tab)로 폴백 → 값이 실제 들어갔는지 확인
        if not await H.ws_set_value(page, M_NUM, num):
            try:
                await page.fill(M_NUM, num, timeout=5000)
                await page.locator(M_NUM).press("Tab")
            except Exception:
                await H.js_fill(page, M_NUM, num)
        await asyncio.sleep(0.3)
        val = await page.evaluate(
            """() => {
                const el = document.getElementById(
                    'mf_txppWframe_UTEETZZA21_wframe_txprDscmNoA');
                return el ? el.value : '';
            }""")
        if re.sub(r"\D", "", val) != num:
            log(f"    [!] 번호 입력이 지워짐(시도 {attempt}) — 다시 입력")
            await asyncio.sleep(0.5)
            continue

        n0 = len(dialogs)
        # 모달 안 클릭은 전부 JS — locator 클릭은 모달 스크롤이 흔들리며 timeout(라이브)
        if not await H.js_click(page, M_SEARCH):
            return False, "모달 조회 버튼 클릭 실패"
        try:
            await page.wait_for_selector(M_RADIO0, state="attached", timeout=10000)
        except Exception:
            msg = "; ".join(m[:50] for m in dialogs[n0:])
            await H.js_click(page, M_CLOSE)
            return False, "수임사업자 검색 결과 없음" + (f" — {msg}" if msg else "")
        await asyncio.sleep(0.5)   # 그리드 갱신 여유 (이전 결과가 남아있을 수 있음)

        rows = await _grid_rows()
        target = next((i for i, t in enumerate(rows)
                       if num and num in re.sub(r"\D", "", t)), None)
        if target is not None:
            break
        log(f"    [!] 검색 필터 미적용(시도 {attempt}, 행 {len(rows)}개) — 다시 검색")

    if target is None:
        await H.js_click(page, M_CLOSE)
        return False, (f"검색 결과에 사업자번호 일치 행 없음 "
                       f"(행 {len(rows)}개, 3회 시도)")
    if len(rows) > 1:
        log(f"    결과 {len(rows)}행 중 {target + 1}행 선택 (번호 일치)")

    if not await H.js_click(page, M_RADIO_FMT.format(target)):
        await H.js_click(page, M_CLOSE)
        return False, "결과 행 라디오 클릭 실패"
    await asyncio.sleep(0.4)
    if not await H.js_click(page, M_OK):
        return False, "확인 버튼 클릭 실패"
    try:
        await page.wait_for_selector(M_OK, state="hidden", timeout=10000)
    except Exception:
        return False, "확인 후 모달이 닫히지 않음"
    log("    수임사업자 전환 완료")
    return True, ""


async def _detail_total(page) -> str:
    """하단 명세 건수 표시(txtTotal) — 명세서 조회 완료 감지용 ('' = 요소 없음)."""
    try:
        return await page.evaluate(
            """() => {
                const el = document.getElementById('mf_txppWframe_txtTotal');
                return el ? (el.innerText || '').trim() : '';
            }""") or ""
    except Exception:
        return ""


async def _current_biz_info(page) -> str:
    """화면 상단 '[ 현재 조회되는 사업자는 … ]' 문구 (없으면 '')."""
    try:
        return await page.evaluate(
            """() => {
                const el = [...document.querySelectorAll('p, div, span')]
                    .find(e => e.offsetParent
                          && (e.innerText || '').includes('현재 조회되는 사업자는')
                          && e.innerText.length < 120);
                return el ? el.innerText.trim() : '';
            }""") or ""
    except Exception:
        return ""


async def _subtotal_rows(page) -> list[str]:
    """화면의 '소계' 행 텍스트들 (위=전송기간 내, 아래=전송기간 외)."""
    return await page.evaluate(
        """() => {
            const rows = [];
            document.querySelectorAll('tr').forEach(tr => {
                if (!tr.offsetParent) return;
                const t = (tr.innerText || '').trim().replace(/\\s+/g, ' ');
                if (t.startsWith('소계')) rows.push(t);
            });
            return rows;
        }""")


def _row_total(row_text: str) -> int:
    return sum(int(n.replace(",", "")) for n in re.findall(r"[\d,]+", row_text))


async def run_hapgye(ctx, client: dict, inp: Inputs, emit, dialogs,
                     *, key: str, label: str, doc: str, kind: str,
                     stop_check=None) -> PhaseResult:
    def log(m):
        emit("log", text=m)

    res = PhaseResult(key, label, client_name=client.get("name", ""))
    page = await H.goto_url(ctx, URL, log=log, ready=SEL_PERIOD)

    # ── ① 분류 라디오 (세금계산서/계산서) ──
    if not await H.check_radio(page, SEL_KIND[kind], log):
        res.reason = "분류 라디오 선택 실패"
        return res
    await asyncio.sleep(0.5)

    # ── ② 수임사업자 선택 — 이미 이 업체로 선택돼 있으면 전환 생략 ──
    # (수임사업자는 세션에 유지됨 — ②에서 선택하면 ③은 라디오만 바꾸면 됨, 사용자 확인)
    num = re.sub(r"\D", "", client.get("bizno", ""))
    info = await _current_biz_info(page)
    if num and num in re.sub(r"\D", "", info):
        log("    수임사업자 이미 선택됨 — 전환 생략")
    else:
        ok, why = await _select_client_via_modal(page, client, dialogs, log)
        if not ok:
            res.reason = why
            return res
        # 화면 상단 "[ 현재 조회되는 사업자는 221-87-03376 ○○○ 입니다. ]"로 반영 검증
        info = await _current_biz_info(page)
        if info:
            if num not in re.sub(r"\D", "", info):
                res.reason = f"수임사업자 전환 미반영 — 화면: {info[:60]}"
                return res
            log(f"    {info[:70]}")

    # ── ③ 분기별 선택 ──
    rtype = effective_report_type(client, inp)
    want = period_label(inp.term, rtype)
    if await H.js_select(page, SEL_PERIOD, want):
        log(f"    분기별: {want}")
    else:
        try:
            opts = await page.locator(f"{SEL_PERIOD} option").all_inner_texts()
        except Exception:
            opts = []
        res.reason = f"분기별 옵션 '{want}' 없음 (현재: {' | '.join(opts)})"
        return res

    # ── ④ 매출 → 매입 각각: 조회 → 전송기간 내/외 명세서 조회 → 명세서 출력 ──
    printed, errs = 0, []
    for side in SIDES:
        if stop_check and stop_check():
            break
        log(f"    ── {side} ──")
        if not await H.check_radio(page, SEL_SIDE[side], log):
            errs.append(f"{side} 라디오 선택 실패")
            continue
        await asyncio.sleep(1.0)

        # 조회 — 완료 신호: 소계 행이 조회 전과 달라짐. 라이브 확인됨(사용자 스크린샷):
        # 매입 라디오 전환 시 표가 빈칸으로 초기화되고 조회를 눌러야 숫자가 채워진다.
        rows_before = await _subtotal_rows(page)
        n0 = len(dialogs)
        if not await H.click_button(page, *BTN_SEARCH, log):
            errs.append(f"{side} 조회 버튼 클릭 실패")
            continue

        async def loaded() -> bool:
            rows_now = await _subtotal_rows(page)
            if rows_now != rows_before:
                return any(re.search(r"\d", r) for r in rows_now)
            return False

        state = await H.wait_loaded_or_bizno_error(dialogs, n0, loaded,
                                                   timeout_sec=20)
        if state == "bizno":
            res.fatal = True
            res.reason = "사업자등록번호 오류 — 홈택스 알림"
            return res
        if state == "timeout":
            log(f"    [!] {side}: 소계 변화 미감지(20초) — 현재 값으로 진행")

        rows = await _subtotal_rows(page)
        parts = [("전송기간내", BTN_DETAIL_IN, rows[0] if rows else ""),
                 ("전송기간외", BTN_DETAIL_OUT, rows[1] if len(rows) > 1 else "")]

        for part_name, detail_btn, row in parts:
            if stop_check and stop_check():
                break
            if row and _row_total(row) == 0:
                log(f"    {side} {part_name}: 소계 0 — 건너뜀")
                await asyncio.sleep(H.NO_RESULT_PAUSE)
                continue
            n1 = len(dialogs)
            total_before = await _detail_total(page)
            clicked = False
            try:
                await page.locator(detail_btn).click(timeout=5000)
                clicked = True
            except Exception:
                clicked = await H.js_click(page, detail_btn)
            if not clicked:
                errs.append(f"{side} {part_name} 명세서 조회 클릭 실패")
                continue
            # 명세 로딩 감지: 건수 표시(txtTotal) 변화 또는 '없습니다' alert —
            # 신호가 오면 즉시 진행, 없으면 기존처럼 최대 3초 대기 (보수적 단축)
            deadline = asyncio.get_event_loop().time() + 3.0
            while asyncio.get_event_loop().time() < deadline:
                if any(NO_RESULT_KEY in m for m in dialogs[n1:]):
                    break
                if await _detail_total(page) != total_before:
                    await asyncio.sleep(0.3)   # 렌더 마무리 여유
                    break
                await asyncio.sleep(0.25)
            if any(NO_RESULT_KEY in m for m in dialogs[n1:]):
                log(f"    {side} {part_name}: 조회결과 없음 — 건너뜀")
                await asyncio.sleep(H.NO_RESULT_PAUSE)
                continue

            out = None
            if inp.output_mode == "pdf":
                out = H.prepare_target(
                    H.client_dir(inp, client) /
                    f"{H.out_name(client, f'{doc}_{side}_{part_name}', inp)}.pdf", log)
            ok_p, err_p = await H.print_via_button(ctx, page, *BTN_PRINT_DETAIL,
                                                   out, inp, log=log)
            if ok_p:
                printed += 1
                if out is not None:
                    res.outputs.append(str(out))
            else:
                errs.append(f"{side} {part_name} 출력 실패: {err_p}")

    res.ok = not errs
    if errs:
        res.reason = "; ".join(errs)
    elif printed == 0:
        res.reason = "매출·매입 모두 자료 없음 — 출력 생략"
    return res
