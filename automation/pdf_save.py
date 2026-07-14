"""Windows "다른 이름으로 저장" 다이얼로그를 자동 채우는 헬퍼 (종소세 프로젝트 검증).

--kiosk-printing + sticky 'Microsoft Print to PDF' 조합에서 인쇄 trigger 후 뜨는
저장 다이얼로그를 pywinauto로 잡아 파일명 입력 + 저장 클릭. 백그라운드 동작
(Windows 메시지 직접 전송 — focus 안 가져감).
"""
import time
from pathlib import Path

# 한국어/영어 다이얼로그 제목 모두 매칭하는 정규식.
# Windows 버전/언어에 따라 표현이 다양함:
#   "다른 이름으로 저장" / "다른 이름으로 PDF 인쇄 출력 저장"
#   "다음 이름으로 프린터 출력 저장"  ← Windows 11 한국어
#   "Save Print Output As" / "Save As"
SAVE_DIALOG_TITLE_RE = (
    r"(다(른|음) 이름으로.*저장"
    r"|Save Print Output As|Save As)"
)


def wait_for_save_dialog(timeout_sec: float = 30.0, poll_interval: float = 0.3):
    """다이얼로그 윈도우 핸들을 찾아 반환. 못 찾으면 None."""
    from pywinauto import findwindows

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            handles = findwindows.find_windows(title_re=SAVE_DIALOG_TITLE_RE)
            if handles:
                return handles[0]
        except Exception:
            pass
        time.sleep(poll_interval)
    return None


def _send_with_timeout(hwnd: int, msg: int, wParam: int, lParam, timeout_ms: int = 1000):
    """SendMessageTimeoutW 호출. block 시간을 timeout_ms로 제한."""
    import ctypes
    user32 = ctypes.windll.user32
    SMTO_ABORTIFHUNG = 0x0002
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(
        hwnd, msg, wParam, lParam,
        SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result),
    )
    return result.value


def fill_and_save(target_path, timeout_sec: float = 30.0, log=None,
                  overwrite_wait_sec: float = 5.0) -> tuple:
    """다음에 뜨는 저장 다이얼로그를 잡아 target_path로 저장. 백그라운드 동작.

    overwrite_wait_sec: 덮어쓰기 확인창 감시 시간 — 호출 전에 기존 파일을
        지우고 시작하는 경우(prepare_target) 확인창이 뜰 일이 없으므로 짧게 줘서
        저장 후 팝업 닫기까지의 대기를 줄인다.
    return: (success: bool, error_message: str|None)
    """
    import ctypes

    target_path = Path(target_path)
    _log = log if log is not None else (lambda s: None)

    user32 = ctypes.windll.user32
    WM_SETTEXT = 0x000C
    WM_COMMAND = 0x0111
    BM_CLICK = 0x00F5
    IDOK = 1

    # 처리 시작 시각 기록 — 끝나고 파일 mtime이 이 후인지로 실제 저장 검증
    start_ts = time.time()

    _log(f"    PDF: 다이얼로그 대기 중... (최대 {int(timeout_sec)}초)")
    handle = wait_for_save_dialog(timeout_sec=timeout_sec)
    if handle is None:
        return False, f"다이얼로그가 {timeout_sec}초 안에 뜨지 않음"
    _log(f"    PDF: 다이얼로그 잡음 (handle={handle})")

    try:
        from pywinauto import Application
        app = Application(backend="win32").connect(handle=handle)
        dlg = app.window(handle=handle)

        path_str = str(target_path)
        path_wchar = ctypes.c_wchar_p(path_str)

        # ComboBoxEx32 → ComboBox → Edit 트리를 따라가며 모두에 set 시도
        targets_set = []

        try:
            combos_ex = dlg.descendants(class_name="ComboBoxEx32")
            for combo_ex in combos_ex:
                try:
                    ch = int(combo_ex.handle)
                    _send_with_timeout(ch, WM_SETTEXT, 0, path_wchar, 1200)
                    targets_set.append(f"ComboBoxEx32({ch})")
                except Exception:
                    pass
                try:
                    inner_combos = combo_ex.descendants(class_name="ComboBox")
                    for ic in inner_combos:
                        try:
                            ich = int(ic.handle)
                            _send_with_timeout(ich, WM_SETTEXT, 0, path_wchar, 1200)
                            targets_set.append(f"ComboBox({ich})")
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    inner_edits = combo_ex.descendants(class_name="Edit")
                    for ie in inner_edits:
                        try:
                            ieh = int(ie.handle)
                            _send_with_timeout(ieh, WM_SETTEXT, 0, path_wchar, 1200)
                            targets_set.append(f"InnerEdit({ieh})")
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # 추가로 직접 자식 Edit들에도 시도 (트리 밖에 있을 수 있음)
        try:
            for edit in dlg.descendants(class_name="Edit"):
                try:
                    eh = int(edit.handle)
                    _send_with_timeout(eh, WM_SETTEXT, 0, path_wchar, 800)
                except Exception:
                    continue
        except Exception:
            pass

        if not targets_set:
            _log("    PDF: [!] ComboBoxEx32 set 대상 못 찾음 — Edit fallback만 시도됨")
        else:
            _log(f"    PDF: set 시도 대상: {', '.join(targets_set)}")

        # 잠시 대기 (ComboBoxEx32 내부 동기화 시간)
        time.sleep(0.3)

        # OK 보냄
        _send_with_timeout(int(handle), WM_COMMAND, IDOK, 0, 1500)
        _log("    PDF: WM_COMMAND IDOK 전송")

        # 덮어쓰기 확인 다이얼로그 polling (파일이 이미 있으면 1~3초 후 뜸)
        time.sleep(0.3)
        _handle_overwrite_dialog(timeout_sec=overwrite_wait_sec, log=_log)

        # 다이얼로그 닫힘 확인 (5초 대기)
        deadline = time.time() + 5
        closed = False
        while time.time() < deadline:
            if not user32.IsWindow(int(handle)):
                closed = True
                break
            time.sleep(0.2)

        # 안 닫혔으면 저장 Button BM_CLICK fallback
        if not closed:
            try:
                buttons = dlg.descendants(class_name="Button")
                for btn in buttons:
                    try:
                        title = (btn.window_text() or "").strip()
                        if title in ("저장(&S)", "저장", "Save", "OK"):
                            bh = int(btn.handle)
                            user32.SendMessageW(bh, BM_CLICK, 0, 0)
                            _log(f"    PDF: BM_CLICK fallback ('{title}' 버튼)")
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.5)
            _handle_overwrite_dialog(timeout_sec=3.0, log=_log)

        # 파일 생성 대기 — mtime이 start_ts 이후여야 진짜 새로 저장된 것
        deadline = time.time() + 15
        while time.time() < deadline:
            if target_path.exists():
                try:
                    mtime = target_path.stat().st_mtime
                    if mtime >= start_ts - 1:  # 1초 여유 (시계 오차 대비)
                        _log(f"    PDF: ✓ 저장 완료 ({target_path.name})")
                        return True, None
                except Exception:
                    pass
            time.sleep(0.2)

        if target_path.exists():
            try:
                mtime_diff = time.time() - target_path.stat().st_mtime
                return False, f"파일이 존재하지만 새로 저장되지 않음 (mtime {mtime_diff:.0f}초 전)"
            except Exception:
                pass
        return False, "OK 보냈으나 파일이 생성되지 않음 (다이얼로그 형식 다를 가능성)"

    except Exception as e:
        return False, f"다이얼로그 자동화 예외: {e}"


def _handle_overwrite_dialog(timeout_sec: float = 5.0, log=None) -> bool:
    """파일이 이미 있을 때 뜨는 덮어쓰기 확인 다이얼로그를 잡아 '예' 클릭."""
    import ctypes
    from pywinauto import Application, findwindows

    user32 = ctypes.windll.user32
    BM_CLICK = 0x00F5
    _log = log if log is not None else (lambda s: None)

    overwrite_title_re = r"(다(른|음) 이름으로 저장 확인|Confirm Save As)"

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            handles = findwindows.find_windows(title_re=overwrite_title_re)
        except Exception:
            handles = []
        for h in handles:
            try:
                app = Application(backend="win32").connect(handle=h)
                dlg = app.window(handle=h)
                for btn in dlg.descendants(class_name="Button"):
                    try:
                        title = (btn.window_text() or "").strip()
                        if title in ("예(&Y)", "예", "Yes"):
                            user32.SendMessageW(int(btn.handle), BM_CLICK, 0, 0)
                            _log("    PDF: 덮어쓰기 다이얼로그 '예' 자동 클릭")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        time.sleep(0.2)
    return False


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Windows 파일명에 못 쓰는 문자 제거 + 길이 제한."""
    import re
    if not name:
        return "_"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = name.strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        return "_"
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name
