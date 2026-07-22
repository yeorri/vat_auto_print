"""공용 유틸 — WebSquare 다층 클릭, 그리드 안정 대기 등 (종소세·인건비 프로젝트 검증)."""
from __future__ import annotations

import asyncio
import re


def norm_bizno(s: str) -> str:
    """하이픈/공백 제거한 사업자번호 10자리. 아니면 ''."""
    d = re.sub(r"\D", "", str(s or ""))
    return d if len(d) == 10 else ""


def norm_regno(s: str) -> str:
    """등록번호 — 사업자번호(10) 또는 주민등록번호(13) 허용. 아니면 ''.

    합계표 수임사업자전환 모달은 주민번호 검색도 지원하므로 명부에 13자리가 와도
    받아둔다 (사업자번호가 필요한 화면은 각 phase에서 자릿수를 검사).
    """
    d = re.sub(r"\D", "", str(s or ""))
    return d if len(d) in (10, 13) else ""


def fmt_bizno(s: str) -> str:
    """'0000000000' → '000-00-00000' (표시용)."""
    d = norm_bizno(s)
    return f"{d[:3]}-{d[3:5]}-{d[5:]}" if d else str(s)


# ── 납부서 파일명 템플릿 ──
# 템플릿 청크: {업체명}, {납부기한}. 그 외 텍스트는 그대로.
# 납부기한 형식은 토큰 자유 입력: YYYY/YY/MM/DD (예: "YY.MM.DD" → 26.07.27).

def parse_due_date(s: str):
    """납부기한 입력 파싱 — '2026-07-27' / '2026.7.27' / '20260727' 등 허용.

    return (year, month, day) 또는 None.
    """
    d = re.findall(r"\d+", str(s or ""))
    if len(d) == 1 and len(d[0]) == 8:          # 20260727
        d = [d[0][:4], d[0][4:6], d[0][6:]]
    if len(d) != 3:
        return None
    try:
        y, m, dd = int(d[0]), int(d[1]), int(d[2])
    except ValueError:
        return None
    if y < 100:                                  # 26.07.27 식 입력도 허용
        y += 2000
    if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= dd <= 31):
        return None
    return y, m, dd


def format_due(due_date: str, fmt: str) -> str:
    """납부기한을 토큰 형식으로 — YYYY/YY/MM/DD 치환 (긴 토큰 먼저)."""
    p = parse_due_date(due_date)
    if p is None:
        return ""
    y, m, d = p
    out = str(fmt or "YY.MM.DD")
    for token, val in (("YYYY", f"{y:04d}"), ("YY", f"{y % 100:02d}"),
                       ("MM", f"{m:02d}"), ("DD", f"{d:02d}")):
        out = out.replace(token, val)
    return out


def render_slip_name(template: str, client_name: str, due_date: str,
                     due_fmt: str) -> str:
    """납부서 파일명(확장자 제외) — {업체명}/{납부기한} 치환."""
    t = str(template or "").strip() or "[납부서]부가가치세_{업체명}_{납부기한}"
    return (t.replace("{업체명}", str(client_name or ""))
             .replace("{납부기한}", format_due(due_date, due_fmt)))


def unknown_template_tokens(template: str) -> list[str]:
    """템플릿의 알 수 없는 {토큰} 목록 — 시작 전 검증용 (오타 방지)."""
    return [t for t in re.findall(r"\{([^{}]*)\}", str(template or ""))
            if t not in ("업체명", "납부기한")]


async def websquare_click(scope, comp_id: str, log=print) -> bool:
    """홈택스 WebSquare 컴포넌트 클릭 — 다층 전략.

    일반 Playwright click이 안 먹는 경우가 많아 아래 순서로 시도:
      1) WebSquare API: getComponentById(id).trigger('onclick')
      2) Playwright 일반 click (id로)
      3) JS dispatchEvent
    """
    # 1) WebSquare API 직접 호출
    try:
        ok = await scope.evaluate(
            """(id) => {
                if (window.WebSquare && WebSquare.util) {
                    const c = WebSquare.util.getComponentById(id);
                    if (c && c.trigger) { c.trigger('onclick'); return true; }
                }
                return false;
            }""",
            comp_id,
        )
        if ok:
            return True
    except Exception as e:
        log(f"[!] websquare api click 실패({comp_id}): {str(e)[:80]}")

    # 2) 일반 click
    try:
        loc = scope.locator(f"#{comp_id}").first
        if await loc.count() > 0:
            await loc.click(timeout=5000)
            return True
    except Exception as e:
        log(f"[!] 일반 click 실패({comp_id}): {str(e)[:80]}")

    # 3) JS dispatchEvent
    try:
        ok = await scope.evaluate(
            """(id) => {
                const el = document.getElementById(id);
                if (!el) return false;
                el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                return true;
            }""",
            comp_id,
        )
        return bool(ok)
    except Exception as e:
        log(f"[!] dispatchEvent click 실패({comp_id}): {str(e)[:80]}")

    return False


async def wait_grid_stable(scope, tbody_sel: str, idle_sec: float = 1.2,
                           timeout_sec: float = 20) -> int:
    """그리드 row 수가 idle_sec 동안 안 변하면 안정으로 보고 row 수 반환."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    last_count, last_change = -1, loop.time()
    while loop.time() < deadline:
        try:
            current = await scope.locator(f"{tbody_sel} tr").count()
        except Exception:
            current = last_count
        if current != last_count:
            last_count, last_change = current, loop.time()
        elif loop.time() - last_change >= idle_sec:
            return max(last_count, 0)
        await asyncio.sleep(0.2)
    return max(last_count, 0)
