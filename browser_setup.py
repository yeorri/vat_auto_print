"""Playwright Chromium 준비 — 동봉 대신 첫 실행 때 1회 다운로드(배포 용량 절감).

배포 zip에서 Chromium(~530MB)을 빼고, 앱 첫 실행 시 Playwright 표준 공용 위치
(%LOCALAPPDATA%\\ms-playwright)에 한 번만 설치한다. 이후 업데이트 zip은 수십 MB.
공용 위치라 같은 방식의 다른 프로그램(yangdo 등)과 Chromium을 공유할 수 있다.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # 드라이버 출력의 터미널 색상 코드 제거용


def _browsers_dir() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"


def browsers_ready() -> bool:
    """Chromium 실행파일이 설치돼 있는지."""
    d = _browsers_dir()
    try:
        return any(d.glob("chromium-*/chrome-win*/chrome.exe")) \
            or any(d.glob("chromium-*/chrome-win/chrome.exe"))
    except Exception:
        return False


def install_browsers(log=print) -> bool:
    """Playwright 드라이버 CLI로 'install chromium' 실행 (다운로드 ~150MB, 인터넷 필요).

    frozen(exe) 환경에서도 동봉된 드라이버(node)로 동작한다. 진행 로그를 log로 중계.
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        exe = compute_driver_executable()
        # 버전에 따라 (node, cli.js) 튜플 또는 단일 경로.
        # --no-shell: 우리가 안 쓰는 Headless Shell(~90MB)은 받지 않음 — 첫 실행 단축.
        cmd = ([*map(str, exe)] if isinstance(exe, (tuple, list)) else [str(exe)]) \
            + ["install", "chromium", "--no-shell"]
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            cmd, env=get_driver_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = _ANSI.sub("", line).strip()
            if line:
                log(f"    다운로드: {line[:90]}")
        code = proc.wait()
        ok = code == 0 and browsers_ready()
        if not ok:
            log(f"[!] 브라우저 설치 종료 코드 {code}")
        return ok
    except Exception as e:  # noqa: BLE001
        log(f"[!] 브라우저 설치 실패: {str(e)[:120]}")
        return False
