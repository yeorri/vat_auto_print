"""홈택스 공용 헬퍼 — 화면 이동, 버튼 클릭(폴백), 인쇄→PDF 저장, 엑셀 다운로드.

셀렉터는 2026-07-14 실제 로그인 화면 DOM에서 확인한 것 (definitions는 각 phase 모듈에).
WebSquare id는 `mf_txppWframe_` 접두 + 개발자 명명(semantic, 예: inputBsno)과
자동 번호(triggerNNN)가 섞여 있다 — trigger류는 value 텍스트 폴백을 항상 함께 둔다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from . import pdf_save
from .browser import HOMETAX_URL, find_page

# 세무대리/납세관리 하위 화면 직접 URL (tm3lIdx = menuAtag id, 2026-07 확인)
_MENU_URL = ("https://hometax.go.kr/websquare/websquare.html"
             "?w2xPath=/ui/pp/index_pp.xml&tmIdx=06&tm2lIdx=0602000000&tm3lIdx={}")


def menu_url(tm3l_idx: str) -> str:
    return _MENU_URL.format(tm3l_idx)


async def hometax_page(ctx: BrowserContext) -> Page:
    """홈택스 메인 탭 반환 (없으면 새로 연다).

    로그인/공지 팝업도 hometax 도메인이라, 메뉴 프레임(index_pp.xml)이 있는
    탭을 우선한다 — 팝업을 잡아 거기서 goto하는 사고 방지.
    """
    page = find_page(ctx, "index_pp.xml") or find_page(ctx, "hometax.go.kr")
    if page is None:
        page = await ctx.new_page()
        await page.goto(HOMETAX_URL, wait_until="domcontentloaded", timeout=30000)
    return page


async def goto_url(ctx: BrowserContext, url: str, log=print,
                   ready: str | None = None) -> Page:
    """홈택스 탭에서 url로 이동 후 WebSquare 로딩 대기.

    ready 셀렉터를 주면 그것이 보일 때까지 기다리고, 안 보이면 1회 재이동 —
    로그인 직후 첫 메뉴 진입이 늦게 뜨거나 빈 화면으로 빠지는 경우 복구.
    매 업체마다 새로 이동 → 이전 조회 값이 남지 않는다(완료 감지 오탐 방지).
    """
    page = await hometax_page(ctx)
    for attempt in (1, 2):
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if not ready:
            await asyncio.sleep(3.0)  # 준비 신호가 없는 호출은 기존처럼 고정 대기
            return page
        # ready 셀렉터가 있으면 고정 3초 대신: 1.5초 + 셀렉터 대기 + 0.5초 안정화
        await asyncio.sleep(1.5)
        try:
            # 첫 시도는 짧게(10초) — 로그인 직후 첫 진입이 빈 화면으로 빠지는 경우
            # 오래 기다리지 말고 빨리 재이동하는 편이 낫다 (라이브 확인)
            await page.wait_for_selector(
                ready, state="visible",
                timeout=10000 if attempt == 1 else 20000)
            await asyncio.sleep(0.5)  # WebSquare 핸들러 바인딩 여유
            return page
        except Exception:
            if attempt == 1:
                log("[!] 화면 준비 안 됨 — 다시 이동합니다.")
    return page   # 마지막 상태로 반환 — phase가 입력 실패 사유를 기록


async def js_fill(page: Page, css_id: str, value) -> bool:
    """input.value 설정 + input/change 이벤트 — 스크롤 이동 없이 입력.

    Playwright fill은 요소를 화면 안으로 스크롤하며 WebSquare 고정 헤더와 싸워
    스크롤바가 위아래로 흔들린다(라이브 확인) — JS 직접 설정은 recon에서 검증됨.
    """
    el_id = css_id.lstrip("#")
    try:
        return bool(await page.evaluate(
            """([id, v]) => {
                const el = document.getElementById(id);
                if (!el) return false;
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", [el_id, str(value)]))
    except Exception:
        return False


async def js_select(page: Page, css_id: str, label: str) -> bool:
    """select에서 옵션 텍스트(label)로 선택 + change 이벤트 — 스크롤 없이."""
    el_id = css_id.lstrip("#")
    try:
        return bool(await page.evaluate(
            """([id, label]) => {
                const el = document.getElementById(id);
                if (!el) return false;
                const opt = [...el.options].find(o => o.text.trim() === label);
                if (!opt) return false;
                el.value = opt.value;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", [el_id, label]))
    except Exception:
        return False


async def ws_set_value(page: Page, css_id: str, value) -> bool:
    """WebSquare 컴포넌트 API setValue — 내부 모델에 직접 기록.

    DOM에 값을 넣는 방식(fill/js_fill)은 WebSquare가 재초기화하며 지우는 레이스가
    있다(합계표 모달에서 라이브 확인) — setValue는 모델을 직접 쓰므로 레이스가 없다.
    """
    el_id = css_id.lstrip("#")
    try:
        return bool(await page.evaluate(
            """([id, v]) => {
                if (window.WebSquare && WebSquare.util) {
                    const c = WebSquare.util.getComponentById(id);
                    if (c && typeof c.setValue === 'function') {
                        c.setValue(v);
                        return true;
                    }
                }
                return false;
            }""", [el_id, str(value)]))
    except Exception:
        return False


async def js_click(page: Page, css_id: str) -> bool:
    """getElementById().click() — 뷰포트 밖/스크롤 흔들림을 우회하는 클릭."""
    el_id = css_id.lstrip("#")
    try:
        return bool(await page.evaluate(
            """(id) => {
                const el = document.getElementById(id);
                if (!el) return false;
                el.click();
                return true;
            }""", el_id))
    except Exception:
        return False


async def check_radio(page: Page, css_id: str, log=print) -> bool:
    """라디오 선택 — JS click 우선(스크롤 흔들림 없음) → check → force click 폴백."""
    if await js_click(page, css_id):
        return True
    try:
        await page.check(css_id, timeout=5000)
        return True
    except Exception:
        pass
    try:
        await page.locator(css_id).click(force=True, timeout=3000)
        return True
    except Exception as e:
        log(f"[!] 라디오 선택 실패({css_id}): {str(e)[:60]}")
        return False


async def click_button(page: Page, css_id: str, value_text: str, log=print) -> bool:
    """버튼 클릭 — id 클릭 → JS 클릭 → value 텍스트 순 폴백."""
    try:
        await page.locator(css_id).first.click(timeout=5000)
        return True
    except Exception:
        pass
    if await js_click(page, css_id):
        log(f"[i] JS 클릭 사용: {css_id}")
        return True
    try:
        await page.locator(
            f"input[type=button][value='{value_text}']").first.click(timeout=5000)
        log(f"[i] 버튼 value 폴백 사용: '{value_text}'")
        return True
    except Exception as e:
        log(f"[!] 버튼 클릭 실패({value_text}): {str(e)[:80]}")
        return False


BIZNO_ERROR_KEY = "사업자등록번호"   # alert "사업자등록번호를 확인하시기 바랍니다."
NO_RESULT_KEY = "없습니다"           # alert형 "조회결과가 없습니다." (합계표 명세서 조회 등)

# ④⑤⑥은 자료가 없으면 alert가 아니라 화면 그리드에 문구가 표시된다 (라이브 확인)
NO_RESULT_TEXT = "조회된 결과가 없습니다"

# '자료 없음'으로 건너뛰기 전 사용자가 화면을 확인할 시간 (사용자 요청: 일괄 0.5초)
NO_RESULT_PAUSE = 0.5


async def no_result_count(page: Page) -> int:
    """화면에 보이는 '조회된 결과가 없습니다' 문구 개수 (④의 표 3개 개별 판정용).

    조상 요소가 자식 텍스트를 중복 집계하지 않도록 자기 텍스트 노드만 검사.
    """
    try:
        return int(await page.evaluate(
            """() => {
                let n = 0;
                document.querySelectorAll('td, div, p, span').forEach(el => {
                    if (!el.offsetParent) return;
                    const own = [...el.childNodes]
                        .filter(c => c.nodeType === 3)
                        .map(c => c.textContent).join('');
                    if (own.includes('조회된 결과가 없습니다')) n++;
                });
                return n;
            }"""))
    except Exception:
        return 0


async def no_result_visible(page: Page) -> bool:
    """화면에 '조회된 결과가 없습니다' 문구가 보이는지 (④⑤⑥ 무자료 감지)."""
    try:
        return bool(await page.evaluate(
            """() => {
                for (const el of document.querySelectorAll('td, div, p')) {
                    if (!el.offsetParent) continue;
                    if ((el.innerText || '').includes('조회된 결과가 없습니다')) return true;
                }
                return false;
            }"""))
    except Exception:
        return False


async def rows_starting_with(page: Page, prefix: str) -> list[str]:
    """화면의 보이는 tr 중 텍스트가 prefix로 시작하는 행들 (위→아래 순).

    조회 완료 감지('합계'/'소계' 행에 숫자 채워짐)와 0원 판단에 쓴다.
    """
    try:
        return await page.evaluate(
            """(prefix) => {
                const rows = [];
                document.querySelectorAll('tr').forEach(tr => {
                    if (!tr.offsetParent) return;
                    const t = (tr.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t.startsWith(prefix)) rows.push(t);
                });
                return rows;
            }""", prefix)
    except Exception:
        return []


def row_total(row_text: str) -> int:
    """행 텍스트 속 숫자들의 합 (0원 여부 판단용)."""
    import re
    return sum(int(n.replace(",", "")) for n in re.findall(r"[\d,]+", row_text))


async def wait_loaded_or_bizno_error(dialogs: list, n0: int, check_loaded,
                                     timeout_sec: float = 40,
                                     extra_keys: dict | None = None) -> str:
    """조회 클릭 후 감시 — 반환: "ok" | "bizno" | extra_keys의 상태 | "timeout".

    dialogs[n0:]에 사업자등록번호 오류 alert가 오면 "bizno" (업체 전체 중단 신호),
    check_loaded()(async, bool)가 True 되면 "ok".
    extra_keys: {반환상태: alert 메시지 부분문자열} — 해당 alert 감지 시 그 상태 반환
    (예: 통합조회의 "조회권한이 없습니다" — 자동 수락돼 화면엔 흔적이 없음).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        for m in dialogs[n0:]:
            if BIZNO_ERROR_KEY in m:
                return "bizno"
            for state, key in (extra_keys or {}).items():
                if key in m:
                    return state
        try:
            if await check_loaded():
                return "ok"
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return "timeout"


def client_dir(inp, client: dict) -> Path:
    """업체별 저장 폴더: {output_dir}/{업체명} — output_dir 지정 시에만 호출할 것.

    pdf 모드의 PDF와 인쇄/pdf 모드의 판매대행 엑셀이 모두 이 폴더에 저장된다.
    """
    d = Path(inp.output_dir) / pdf_save.sanitize_filename(client.get("name", "_"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_name(client: dict, doc_label: str, inp) -> str:
    """저장 파일명: '업체명_자료명_2026년1기_확정' (폴더가 업체별이라 이름 중복 안전).

    끝의 신고구분은 GUI에서 고른 신고시즌(확정/예정) — 업체별 예정+확정 여부와
    무관하게 같은 실행이면 같은 접미사(사용자 요청: 시즌 표시용).
    """
    base = (f"{client.get('name', '')}_{doc_label}"
            f"_{inp.year}년{inp.term}기_{inp.season}")
    return pdf_save.sanitize_filename(base)


def prepare_target(out_path: Path, log=print) -> Path:
    """저장 대상 사전 정리 — 같은 이름 파일이 있으면 지우고 시작.

    덮어쓰기 확인 다이얼로그(pdf_save가 '예' 자동클릭)와 별개로, OneDrive 동기화가
    파일을 잡고 있으면 덮어쓰기 자체가 실패해 Chrome '인쇄 실패'가 뜬다(라이브 추정)
    — 미리 지우면 두 변수 모두 회피. 잠겨서 못 지우면 시각을 붙인 새 이름 반환.
    """
    if not out_path.exists():
        return out_path
    try:
        out_path.unlink()
        log(f"    기존 파일 삭제 후 재저장: {out_path.name}")
        return out_path
    except Exception:
        from datetime import datetime
        new = out_path.with_name(
            f"{out_path.stem}_{datetime.now():%H%M%S}{out_path.suffix}")
        log(f"    기존 파일 잠김 — 새 이름으로 저장: {new.name}")
        return new


# clipReport 뷰어의 [인쇄] 버튼 — id는 랜덤 해시라 클래스로 잡는다 (2026-07-14 확인).
# 팝업 구조: popup.html → iframe 'reportFrame_0'(sesw…/clipreport.do) → 인쇄 버튼
REPORT_PRINT_BTN = "button.report_menu_print_button"


def _find_report_page(ctx: BrowserContext, before: set) -> Page | None:
    """인쇄용 Report 창 찾기 — 새로 뜬 창 우선, 없으면 재사용된 기존 popup/Report 창.

    WebSquare는 같은 popupID 창을 재사용하므로 '새 창 없음 = 팝업 없음'이 아니다.
    """
    for p in ctx.pages:
        if p not in before:
            return p
    for p in ctx.pages:
        url = (p.url or "").lower()
        if "popup.html" in url or "clipreport" in url:
            return p
    return None


async def _wait_report_page(ctx: BrowserContext, before: set,
                            timeout_sec: float = 3.0) -> Page | None:
    """새 인쇄 팝업을 폴링으로 대기 — 뜨는 즉시 반환(고정 3초 대기 대체).

    timeout까지 새 창이 없으면 재사용된 기존 popup/Report 창을 확인해 반환.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        for p in ctx.pages:
            if p not in before:
                return p
        await asyncio.sleep(0.25)
    return _find_report_page(ctx, before)


async def _click_report_print(popup: Page, log, timeout_sec: float = 25,
                              dialog_seen=None) -> bool:
    """Report 뷰어의 [인쇄] 버튼을 모든 프레임에서 찾아 클릭 (렌더 완료까지 폴링).

    window.print()로 팝업 페이지를 통째로 찍으면 뷰어 껍데기가 인쇄되는 오류
    (라이브 확인) — 반드시 뷰어 자체의 인쇄 버튼을 눌러야 보고서가 제대로 나온다.

    뷰어 초기화 전에 클릭하면 씹혀서 패널이 안 열린다(라이브 확인) —
    버튼 발견 후 1초 안정화하고, 클릭 후 패널/저장다이얼로그 신호가 없으면 재클릭.
    dialog_seen: 저장 다이얼로그가 이미 떴는지 알려주는 콜백(pdf 모드) —
        패널 없이 바로 인쇄되는 뷰어(⑤⑥)에서 재클릭으로 이중 인쇄되는 것을 막는다.
    """
    loop = asyncio.get_event_loop()

    async def _find_frame():
        deadline = loop.time() + timeout_sec
        while loop.time() < deadline:
            for fr in popup.frames:
                try:
                    found = await fr.evaluate(
                        """() => {
                            const b = document.querySelector(
                                'button.report_menu_print_button');
                            return !!(b && b.offsetParent);
                        }""")
                except Exception:
                    continue
                if found:
                    return fr
            await asyncio.sleep(0.5)
        return None

    fr = await _find_frame()
    if fr is None:
        return False
    await asyncio.sleep(1.0)   # 뷰어 스크립트 초기화 여유 — 이르게 누르면 씹힘

    for attempt in (1, 2, 3):
        try:
            # locator 클릭은 팝업 바깥 스크롤을 흔들어(라이브 확인) JS로만 클릭
            clicked = await fr.evaluate(
                """() => {
                    const b = document.querySelector(
                        'button.report_menu_print_button');
                    if (!b || !b.offsetParent) return false;
                    b.click();
                    return true;
                }""")
        except Exception:
            clicked = False
        if not clicked:
            await asyncio.sleep(0.5)
            continue
        log("    뷰어 인쇄 버튼 클릭" + (f" (재시도 {attempt})" if attempt > 1 else ""))

        # 패널([인쇄] 버튼) 또는 저장 다이얼로그가 나타날 때까지 최대 4초 감시
        signal_deadline = loop.time() + 4.0
        while loop.time() < signal_deadline:
            try:
                panel = await fr.evaluate(
                    """() => {
                        const btns = [...document.querySelectorAll(
                            'button, input[type=button]')].filter(b =>
                            b.offsetParent
                            && !(b.className || '').toString()
                                .includes('report_menu_print_button')
                            && ((b.innerText || b.value || '').trim() === '인쇄'));
                        if (!btns.length) return false;
                        btns[0].click();
                        return true;
                    }""")
            except Exception:
                panel = False
            if panel:
                log("    인쇄방식 패널 [인쇄] 클릭")
                return True
            if dialog_seen is not None and dialog_seen():
                return True   # 패널 없는 뷰어 — 이미 인쇄(저장 다이얼로그) 진행 중
            await asyncio.sleep(0.25)

        if dialog_seen is None:
            # print 모드: 신호를 알 수 없음 — 이중 인쇄 위험이 있어 재클릭하지 않음
            return True
        log("    [!] 인쇄 반응 없음 — 뷰어 인쇄 버튼 재클릭")

    return True   # 클릭은 했음 — 최종 성패는 저장 다이얼로그 결과로 판정


async def print_via_button(ctx: BrowserContext, page: Page, css_id: str,
                           value_text: str, out_path: Path | None, inp,
                           log=print) -> tuple[bool, str]:
    """인쇄 버튼 클릭 → 모드별 처리. Report 뷰어 팝업이 뜨면 뷰어 인쇄 버튼 클릭.

    - 직접 인쇄형(① 통합조회): 클릭 즉시 kiosk-printing이 처리.
    - 팝업형(②④⑤⑥): 팝업 안 clipReport 뷰어의 [인쇄] 버튼을 눌러야 함.
    - print 모드: sticky 기본 프린터로 출력 / pdf 모드: 저장 다이얼로그를 잡아 저장.
    """
    before = set(ctx.pages)

    if inp.output_mode != "pdf":
        if not await click_button(page, css_id, value_text, log):
            return False, "인쇄 버튼 클릭 실패"
        popup = await _wait_report_page(ctx, before)
        if popup is not None:
            if not await _click_report_print(popup, log):
                await _close_quiet(popup)
                return False, "뷰어 인쇄 버튼을 찾지 못함"
            await asyncio.sleep(4.0)   # 스풀링 여유
            await _close_quiet(popup)
        return True, ""

    # pdf 모드 — 다이얼로그 감시를 먼저 걸어두고(뷰어 로딩 시간 포함 60초) 진행.
    # 덮어쓰기 확인창은 prepare_target(사전 삭제) 덕에 뜰 일이 없어 감시 0.5초만.
    loop = asyncio.get_event_loop()
    state = {"dialog": False}

    def _log_watch(m):
        # pdf_save 진행 로그에서 저장 다이얼로그 등장을 감지 — 뷰어 재클릭 가드용
        if "다이얼로그 잡음" in m:
            state["dialog"] = True
        log(m)

    fut = loop.run_in_executor(
        None, lambda: pdf_save.fill_and_save(out_path, timeout_sec=60, log=_log_watch,
                                             overwrite_wait_sec=0.5))
    if not await click_button(page, css_id, value_text, log):
        return False, "인쇄 버튼 클릭 실패"
    popup = await _wait_report_page(ctx, before)
    if popup is not None:
        if not await _click_report_print(popup, log,
                                         dialog_seen=lambda: state["dialog"]):
            log("[!] 뷰어 인쇄 버튼 미발견 — window.print() 폴백 (레이아웃 이상 가능)")
            try:
                await popup.evaluate("window.print()")
            except Exception:
                pass
    ok, err = await fut
    await _close_quiet(_find_report_page(ctx, before))
    return ok, err or ""


async def _close_quiet(p: Page | None):
    if p is None:
        return
    try:
        await p.close()
    except Exception:
        pass


async def download_excel(page: Page, trigger_download, out_path: Path,
                         log=print) -> tuple[bool, str, str]:
    """엑셀 내려받기 trigger → 다운로드 파일을 out_path로 저장.

    홈택스 '엑셀'은 실제로는 .xls(구형/HTML 표)로 오는 경우가 있어(라이브 확인:
    .xlsx로 저장하면 엑셀이 '확장자 불일치'로 거부) 실제 형식에 확장자를 맞춘다.
    return: (성공, 사유, 실제 저장 경로)
    """
    try:
        async with page.expect_download(timeout=60000) as dl_info:
            await trigger_download()
        dl = await dl_info.value
        # 서버가 알려준 파일명의 확장자를 우선 반영
        sug_ext = Path(dl.suggested_filename or "").suffix.lower()
        if sug_ext and sug_ext != out_path.suffix.lower():
            out_path = out_path.with_suffix(sug_ext)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await dl.save_as(str(out_path))
        # 내용 검사 — xlsx는 zip(PK)이어야 함. 아니면 .xls로 바꿔 저장(엑셀에서 열림)
        try:
            if out_path.suffix.lower() == ".xlsx":
                with open(out_path, "rb") as f:
                    head = f.read(2)
                if head != b"PK":
                    fixed = out_path.with_suffix(".xls")
                    if fixed.exists():
                        fixed.unlink()
                    out_path.rename(fixed)
                    out_path = fixed
                    log("    엑셀: 실제 형식이 xlsx가 아님 — .xls로 저장")
        except Exception:
            pass
        log(f"    엑셀: ✓ 저장 완료 ({out_path.name})")
        return True, "", str(out_path)
    except Exception as e:
        return False, f"엑셀 다운로드 실패: {str(e)[:100]}", ""
