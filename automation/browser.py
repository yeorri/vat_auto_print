"""브라우저 토대 — Playwright 영구 프로필 Chromium 실행 + 홈택스 로그인 대기.

incometax_printing / ingunbi_auto에서 검증된 launch / find_visible / wait_for 패턴.
홈택스 로그인은 봇이 시도하면 안 되므로 사용자가 직접 로그인하도록 두고
'로그아웃' 텍스트 감지로 로그인 완료를 polling한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from playwright.async_api import BrowserContext, Page


def app_data_dir() -> Path:
    """쓰기 가능한 앱 데이터 폴더.

    - 개발(소스 실행): 프로젝트 폴더.
    - 배포(frozen exe): %LOCALAPPDATA%\\VatAutoPrint.
    """
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VatAutoPrint"
    else:
        base = Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def _init_profile_dir() -> Path:
    """홈택스 자동화 프로그램들이 공유하는 브라우저 프로필 폴더.

    Chrome에 저장한 아이디/비밀번호·로그인 세션은 프로필 폴더에 (Windows 계정으로
    암호화되어) 저장된다 — 프로그램마다 프로필이 따로면 매번 다시 저장해야 하므로
    공용 위치를 쓴다. 다른 프로그램(인건비 등)도 이 폴더를 보게 하면 그대로 공유.
    ⚠ 같은 프로필로 두 프로그램을 동시에 켜면 잠금 충돌 — 동시 실행하지 않는 전제.

    이 프로그램의 기존 .profile이 있으면 1회 이관(저장해둔 비밀번호 유지).
    이관 실패(예: 브라우저가 아직 열려 있어 잠김) 시 기존 위치를 계속 쓴다.
    """
    shared = Path(os.environ.get("LOCALAPPDATA") or Path.home()) \
        / "HometaxAutoShared" / ".profile"
    local = app_data_dir() / ".profile"
    if not shared.exists() and local.exists():
        try:
            shared.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(local), str(shared))
        except Exception:
            return local
    shared.mkdir(parents=True, exist_ok=True)
    return shared


# 로그인 세션·저장된 비밀번호 캐시 폴더 (프로그램 간 공유).
PROFILE_DIR = _init_profile_dir()

HOMETAX_URL = "https://www.hometax.go.kr"


async def launch(pw) -> BrowserContext:
    """영구 프로필로 Chromium 실행.

    --kiosk-printing: 인쇄 다이얼로그 없이 sticky 프린터로 바로 출력.
    PDF 모드는 launch 전에 ensure_pdf_sticky_settings()를 먼저 실행해 둘 것.
    """
    PROFILE_DIR.mkdir(exist_ok=True)
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        no_viewport=True,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",  # 봇 감지 회피
            "--kiosk-printing",
            "--remote-debugging-port=9222",  # 개발 중 화면 확인용(로컬 전용)
        ],
        accept_downloads=True,
    )


def ensure_sticky_printer(printer: str, profile_dir: Path = PROFILE_DIR) -> bool:
    """Chrome Preferences의 인쇄 대상(sticky)을 printer로 박는다.

    --kiosk-printing은 OS 기본 프린터가 아니라 이 sticky 값으로 인쇄한다.
    ⚠ Chromium 실행 중에 고치면 종료 시 덮어써짐 → 반드시 launch 전에 호출.
    """
    prefs_path = profile_dir / "Default" / "Preferences"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8")) if prefs_path.exists() else {}
    except Exception:
        prefs = {}
    sticky = {
        "version": 2,
        "recentDestinations": [{
            "id": printer, "origin": "local", "account": "",
            "capabilities": "", "displayName": printer,
            "extensionId": "", "extensionName": "", "icon": "",
        }],
        "selectedDestinationId": printer,
    }
    prefs.setdefault("printing", {}).setdefault("print_preview_sticky_settings", {})
    prefs["printing"]["print_preview_sticky_settings"]["appState"] = json.dumps(sticky)
    try:
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def ensure_pdf_sticky_settings(profile_dir: Path = PROFILE_DIR) -> bool:
    """PDF 저장 모드용 — 인쇄 대상을 'Microsoft Print to PDF'로."""
    return ensure_sticky_printer("Microsoft Print to PDF", profile_dir)


def default_printer_name() -> str:
    """Windows 기본 프린터 이름. 실패 시 ''."""
    try:
        import win32print
        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


def attach_dialog_handler(page: Page, msgs: list) -> None:
    """JS alert/confirm 자동 수락 + 메시지 기록 (분기 검출용)."""
    async def on_dialog(d):
        msgs.append(d.message)
        try:
            await d.accept()
        except Exception:
            pass

    page.on("dialog", on_dialog)


def find_page(ctx: BrowserContext, url_substr: str):
    """ctx의 열린 page 중 url에 substr가 든 첫 page. 없으면 None."""
    for p in ctx.pages:
        if url_substr in (p.url or ""):
            return p
    return None


async def setup_context(ctx: BrowserContext, dialog_msgs: list) -> None:
    """모든 page(새 page 포함)에 dialog 핸들러 부착."""
    for p in ctx.pages:
        attach_dialog_handler(p, dialog_msgs)
    ctx.on("page", lambda p: attach_dialog_handler(p, dialog_msgs))


async def open_hometax(ctx: BrowserContext, log=print) -> Page:
    """홈택스 홈페이지 탭 확보 — 이미 열려 있으면 재사용, 없으면 첫 탭에 연다."""
    page = find_page(ctx, "hometax.go.kr")
    if page is not None:
        return page
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    try:
        await page.goto(HOMETAX_URL, wait_until="domcontentloaded", timeout=30000)
        log("[i] 홈택스 홈페이지 열림")
    except Exception as e:
        log(f"[!] 홈택스 이동 실패: {str(e)[:80]}")
    return page


async def find_visible(ctx: BrowserContext, selector: str):
    """모든 page/frame 순회해서 selector가 보이는 (page, frame)을 반환.

    frame이 detach되면 호출 자체가 throw하므로 try/except 필수.
    """
    for page in ctx.pages:
        for frame in page.frames:
            try:
                loc = frame.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    return page, frame
            except Exception:
                continue
    return None, None


async def wait_for(ctx: BrowserContext, selector: str, timeout_sec: int, log=print):
    """selector가 보일 때까지 1초 polling. (page, frame) 또는 (None, None)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        page, scope = await find_visible(ctx, selector)
        if scope is not None:
            return page, scope
        await asyncio.sleep(1.0)
    return None, None
