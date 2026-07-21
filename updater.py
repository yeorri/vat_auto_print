"""앱 시작 시 GitHub에 호스팅된 version.json을 fetch → 새 버전 있으면 callback.

흐름 (incometax_printing/ingunbi_auto에서 검증된 패턴):
  1. CURRENT_VERSION 상수에 빌드 시점의 버전 박혀 있음.
  2. 앱 시작 후 background thread에서 UPDATE_CHECK_URL을 fetch.
  3. version.json의 "version"이 CURRENT_VERSION보다 크면 callback(info) 호출.
  4. 네트워크 실패/타임아웃 등은 조용히 무시 (사용자 작업 방해 X).

배포 절차:
  1. updater.py의 CURRENT_VERSION 올리기 (예: "1.1.0")
  2. version.json의 "version"/"notes" 갱신 후 main에 push
  3. pyinstaller vat_auto_print.spec --noconfirm → dist 압축
  4. gh release create v1.1.0 VatAutoPrint_v1.1.0.zip
"""
import json
import threading
import urllib.request

# ───── 빌드별로 갱신 ─────
CURRENT_VERSION = "1.0.6"

# ───── GitHub 호스팅 위치 ─────
GITHUB_USER = "yeorri"
GITHUB_REPO = "vat_auto_print"
UPDATE_CHECK_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.json"
DOWNLOAD_PAGE = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"


def _parse_version(v: str) -> tuple:
    """'1.2.3' → (1, 2, 3). 비교 가능한 tuple로."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)


def _check_sync() -> dict | None:
    """동기적으로 한 번 체크. 새 버전 있으면 dict, 아니면 None."""
    try:
        req = urllib.request.Request(
            UPDATE_CHECK_URL,
            headers={"User-Agent": "VatAutoPrint-UpdateCheck"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    latest = (data.get("version") or "").strip()
    if not latest:
        return None
    if _parse_version(latest) <= _parse_version(CURRENT_VERSION):
        return None
    return {
        "latest": latest,
        "current": CURRENT_VERSION,
        "download_url": (data.get("download_url") or "").strip() or DOWNLOAD_PAGE,
        "notes": (data.get("notes") or "").strip(),
    }


def check_async(callback):
    """Background thread에서 체크. 새 버전 있으면 callback(info) 호출.
    callback은 Tk main thread 컨텍스트가 아닐 수 있으므로,
    GUI 조작은 callback 내부에서 root.after(0, ...)로 디스패치해야 안전.
    """
    def _run():
        info = _check_sync()
        if info:
            try:
                callback(info)
            except Exception:
                pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
