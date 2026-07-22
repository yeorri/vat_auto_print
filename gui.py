"""부가세 신고자료 자동 출력 — Tkinter GUI (모던 테마).

업체 명부(엑셀 가져오기)에서 체크한 업체들에 대해, 선택한 조회/출력 작업(phase)을
순서대로 실행한다. 브라우저는 세션으로 유지 — 첫 로그인 후 재로그인 불필요.
순수 Tkinter Canvas 커스텀 테마(외부 의존성 없음) — ingunbi/yangdo와 동일 계열.

실행:  python gui.py
"""
from __future__ import annotations

import os
import sys

# 배포(frozen exe)에서 Chromium 위치 결정 — 어떤 playwright import보다 먼저.
if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    _bundled = os.path.join(_base, "playwright-browsers")
    if os.path.isdir(_bundled):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _bundled)
    else:
        # playwright-python은 frozen이면 PLAYWRIGHT_BROWSERS_PATH='0'을 강제 주입
        # → 공용 위치(ms-playwright)를 명시해 선점해야 브라우저를 찾는다.
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH",
            os.path.join(os.environ.get("LOCALAPPDATA")
                         or os.path.expanduser("~\\AppData\\Local"), "ms-playwright"))

import asyncio
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import browser_setup
import updater
from automation import ALL_PHASES, BrowserSession, Inputs, run_all
from automation import roster
from automation.browser import app_data_dir
from automation.util import (fmt_bizno, norm_regno, parse_due_date,
                             render_slip_name, unknown_template_tokens)

SETTINGS_PATH = app_data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(d: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────── 디자인 토큰 ───────────────────────────
FONT = "Malgun Gothic"
MONO = "Consolas"
BG = "#F1F5F9"                # slate-100  (앱 배경)
CARD = "#FFFFFF"
HEAD = "#0F172A"              # slate-900  (헤더)
INK = "#0F172A"
MUTE = "#64748B"
BORDER = "#E2E8F0"
ACCENT = "#7C3AED"            # violet-600 (다른 앱과 색으로 구분)
ACCENT_DK = "#6D28D9"
ACCENT_SOFT = "#EDE9FE"
TRACK = "#CBD5E1"
CONSOLE_BG = "#0B1220"
CONSOLE_FG = "#E2E8F0"


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Canvas에 둥근 사각형(smooth polygon)."""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


# ─────────────────────────── 커스텀 위젯 ───────────────────────────
class Toggle(tk.Canvas):
    """iOS식 토글 스위치 (BooleanVar 바인딩)."""

    def __init__(self, parent, variable: tk.BooleanVar, bg, command=None):
        super().__init__(parent, width=46, height=26, bg=bg, highlightthickness=0, bd=0)
        self.var = variable
        self.command = command
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        round_rect(self, 2, 3, 44, 23, 10, fill=ACCENT if on else TRACK, outline="")
        x = 26 if on else 4
        self.create_oval(x, 5, x + 16, 21, fill="#FFFFFF", outline="")

    def _click(self, _e):
        self.var.set(not self.var.get())
        if self.command:
            self.command()


class RButton(tk.Canvas):
    """둥근 버튼 (primary / ghost / mini) + hover."""

    def __init__(self, parent, text, command, *, kind="primary", bg,
                 width=128, height=44, font=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.text, self.command, self.kind = text, command, kind
        self.w, self.h = width, height
        self.font = font or (FONT, 11, "bold")
        self._hover = False
        self.enabled = True
        self.configure(cursor="hand2")
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _on_click(self, _e):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, b: bool):
        if b == self.enabled:
            return
        self.enabled = b
        self.configure(cursor="hand2" if b else "arrow")
        self._draw()

    def _set(self, h):
        if not self.enabled:
            return
        self._hover = h
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.w, self.h
        if self.kind == "primary":
            if not self.enabled:
                fill = "#CBD5E1"
            else:
                fill = ACCENT_DK if self._hover else ACCENT
            round_rect(self, 1, 1, w - 1, h - 1, 13, fill=fill, outline="")
            fg = "#FFFFFF" if self.enabled else "#EEF2F8"
        elif self.kind == "ghost":
            round_rect(self, 1, 1, w - 1, h - 1, 13,
                       fill="#F8FAFC" if self._hover else CARD,
                       outline=BORDER, width=1)
            fg = INK
        else:  # mini
            round_rect(self, 1, 1, w - 1, h - 1, 9,
                       fill=ACCENT_SOFT if self._hover else "#F1F5F9", outline="")
            fg = ACCENT
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)


class Segmented(tk.Canvas):
    """N지 세그먼트 컨트롤 (StringVar)."""

    def __init__(self, parent, variable: tk.StringVar, options, bg,
                 width=190, height=36):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.var = variable
        self.options = options  # [(value, label), ...]
        self.w, self.h = width, height
        self.configure(cursor="hand2")
        self.bind("<Button-1>", self._click)
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h, n = self.w, self.h, len(self.options)
        round_rect(self, 1, 1, w - 1, h - 1, 11, fill="#F1F5F9",
                   outline=BORDER, width=1)
        seg = w / n
        for i, (val, label) in enumerate(self.options):
            sel = self.var.get() == val
            cx = seg * i + seg / 2
            if sel:
                x1 = seg * i + 3
                round_rect(self, x1, 3, x1 + seg - 6, h - 3, 9, fill=ACCENT, outline="")
            self.create_text(cx, h / 2, text=label,
                             fill="#FFFFFF" if sel else MUTE, font=(FONT, 9, "bold"))

    def _click(self, e):
        i = min(len(self.options) - 1, max(0, int(e.x / (self.w / len(self.options)))))
        self.var.set(self.options[i][0])


class Pill(tk.Canvas):
    """상태 pill (대기/진행/완료/실패)."""

    STYLES = {
        "idle": ("대기", "#F1F5F9", "#64748B"),
        "run": ("진행 중", "#FEF3C7", "#B45309"),
        "ok": ("완료", "#DCFCE7", "#15803D"),
        "fail": ("실패", "#FEE2E2", "#B91C1C"),
    }

    def __init__(self, parent, bg):
        super().__init__(parent, width=64, height=24, bg=bg,
                         highlightthickness=0, bd=0)
        self.set("idle")

    def set(self, status):
        t, fill, fg = self.STYLES.get(status, self.STYLES["idle"])
        self.delete("all")
        round_rect(self, 1, 1, 63, 23, 11, fill=fill, outline="")
        self.create_text(32, 12, text=t, fill=fg, font=(FONT, 8, "bold"))


# ─────────────────────────── 앱 ───────────────────────────
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("부가세 신고자료 자동 출력")
        root.geometry("1180x900")
        root.minsize(1040, 760)
        root.configure(bg=BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", font=(FONT, 9), rowheight=26,
                        background=CARD, fieldbackground=CARD, borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"),
                        background="#F8FAFC", foreground=MUTE, relief="flat")

        self.events: queue.Queue = queue.Queue()
        self.session: BrowserSession | None = None
        self.session_loop: asyncio.AbstractEventLoop | None = None
        self._busy = False
        self._stop = False
        self._run_fut = None   # 실행 중인 asyncio task — 중지 시 즉시 취소용
        self._phase_vars: dict[str, tk.BooleanVar] = {}
        self._phase_pills: dict[str, Pill] = {}

        s = load_settings()
        self.var_year = tk.StringVar(value=s.get("year", "2026"))
        self.var_term = tk.StringVar(value=s.get("term", "1"))
        self.var_season = tk.StringVar(value=s.get("season", "확정"))
        self.var_summary = tk.BooleanVar(value=s.get("excel_summary", True))
        # 서류 처리 모드는 저장하지 않고 항상 '인쇄'로 시작 (사용자 요청)
        self.var_mode = tk.StringVar(value="print")
        self.var_outdir = tk.StringVar(value=s.get("output_dir", ""))
        # 납부서 출력 모드 (앱 모드는 저장하지 않고 항상 '자료 출력'으로 시작)
        self.var_app_mode = tk.StringVar(value="data")   # "data" | "slip"
        self.var_due_date = tk.StringVar(value=s.get("due_date", ""))
        self.var_due_format = tk.StringVar(value=s.get("due_format", "YY.MM.DD"))
        self.var_slip_template = tk.StringVar(
            value=s.get("slip_template", "[납부서]부가가치세_{업체명}_{납부기한}"))
        self.var_slip_period = tk.StringVar(value=s.get("slip_period", "1개월"))

        # 업체 명부 (clients.json) — 목록에 있는 업체는 전부 실행 대상.
        # 행 클릭 선택은 삭제용(빼고 싶은 업체는 선택해서 삭제).
        self.clients: list[dict] = roster.load_clients()

        self._browsers_ready = browser_setup.browsers_ready()
        self._build_ui()
        for v in (self.var_mode, self.var_outdir, self.var_due_date):
            v.trace_add("write", lambda *a: self._refresh_validation())
        for v in (self.var_due_date, self.var_due_format, self.var_slip_template):
            v.trace_add("write", lambda *a: self._refresh_slip_preview())
        self.var_app_mode.trace_add("write", lambda *a: self._apply_app_mode())
        self._refresh_slip_preview()
        self._refresh_clients()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll)
        updater.check_async(self._on_update_available)
        if not self._browsers_ready:
            self._install_browsers_async()

    def _on_update_available(self, info: dict):
        """새 버전 알림 — background thread에서 호출되므로 Tk 조작은 after로 디스패치."""
        def ask():
            msg = (f"새 버전 v{info['latest']}이 있습니다. (현재 v{info['current']})\n\n"
                   + (f"{info['notes']}\n\n" if info.get("notes") else "")
                   + "다운로드 페이지를 열까요?")
            if messagebox.askyesno("업데이트 알림", msg):
                import webbrowser
                webbrowser.open(info["download_url"])
        try:
            self.root.after(0, ask)
        except Exception:
            pass

    # ── 첫 실행: Chromium 자동 설치 ──
    def _install_browsers_async(self):
        self._append_log("[i] 첫 실행 준비 — 브라우저 구성요소(약 150MB)를 다운로드합니다. "
                         "인터넷 연결이 필요하며 몇 분 걸릴 수 있습니다…")

        def worker():
            ok = browser_setup.install_browsers(
                lambda m: self.events.put({"kind": "log", "text": m}))
            if ok:
                self.events.put({"kind": "log",
                                 "text": "[v] 브라우저 준비 완료 — 이제 시작할 수 있습니다."})

            def fin():
                self._browsers_ready = ok
                self._refresh_validation()
                if not ok:
                    messagebox.showerror(
                        "설치 실패",
                        "브라우저 구성요소 다운로드에 실패했습니다.\n"
                        "인터넷 연결을 확인한 뒤 프로그램을 다시 실행해주세요.")
            self.root.after(0, fin)

        threading.Thread(target=worker, daemon=True).start()

    # ── UI 구성 ──
    def _card(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        tk.Label(outer, text=title, bg=CARD, fg=INK,
                 font=(FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        return outer

    def _build_ui(self):
        # 헤더
        head = tk.Frame(self.root, bg=HEAD, height=72)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="부가세 신고자료 자동 출력", bg=HEAD, fg="#FFFFFF",
                 font=(FONT, 15, "bold")).pack(side="left", padx=(22, 10), pady=16)
        tk.Label(head, text="홈택스 세무대리 — 통합조회·합계표·신용카드·현금영수증·납부서",
                 bg=HEAD, fg="#94A3B8", font=(FONT, 9)).pack(side="left", pady=20)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=14, pady=12)
        left = tk.Frame(main, bg=BG, width=470)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # ── ① 업체 명부 ──
        c1 = self._card(left, "① 업체 명부")
        c1.pack(fill="both", expand=True)
        # 인라인 업체 추가줄: 업체명 / 사업자번호 / 예정신고 O·X / [+ 추가]
        self.v_add_name = tk.StringVar()
        self.v_add_bizno = tk.StringVar()
        self.v_add_yeo = tk.StringVar(value="X")
        addrow = tk.Frame(c1, bg=CARD)
        addrow.pack(fill="x", padx=14, pady=(2, 2))

        def _entry(var, width):
            return tk.Entry(addrow, textvariable=var, width=width, font=(FONT, 9),
                            relief="flat", highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=ACCENT)

        tk.Label(addrow, text="업체명", bg=CARD, fg=MUTE,
                 font=(FONT, 8)).pack(side="left")
        e_name = _entry(self.v_add_name, 11)
        e_name.pack(side="left", padx=(3, 6), ipady=3)
        tk.Label(addrow, text="사업자번호", bg=CARD, fg=MUTE,
                 font=(FONT, 8)).pack(side="left")
        e_biz = _entry(self.v_add_bizno, 10)
        e_biz.pack(side="left", padx=(3, 6), ipady=3)
        tk.Label(addrow, text="예정", bg=CARD, fg=MUTE,
                 font=(FONT, 8)).pack(side="left")
        Segmented(addrow, self.v_add_yeo, [("O", "O"), ("X", "X")], CARD,
                  width=56, height=26).pack(side="left", padx=(3, 6))
        RButton(addrow, "+추가", self._add_client_inline, kind="mini", bg=CARD,
                width=48, height=26, font=(FONT, 9, "bold")).pack(side="left")
        e_biz.bind("<Return>", lambda e: self._add_client_inline())

        bar = tk.Frame(c1, bg=CARD)
        bar.pack(fill="x", padx=14, pady=(4, 6))
        RButton(bar, "엑셀 가져오기", self._import_excel, kind="mini", bg=CARD,
                width=96, height=28, font=(FONT, 9, "bold")).pack(side="left")
        RButton(bar, "선택 삭제", self._delete_selected, kind="mini", bg=CARD,
                width=70, height=28, font=(FONT, 9, "bold")).pack(side="left", padx=(8, 0))
        RButton(bar, "전체 삭제", self._delete_all, kind="mini", bg=CARD,
                width=70, height=28, font=(FONT, 9, "bold")).pack(side="left", padx=(6, 0))
        self.lbl_count = tk.Label(bar, text="", bg=CARD, fg=MUTE, font=(FONT, 9))
        self.lbl_count.pack(side="right")

        wrap = tk.Frame(c1, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.tree = ttk.Treeview(wrap, columns=("name", "bizno", "yeo"),
                                 show="headings", selectmode="extended")
        for col, txt, w, anchor in (("name", "업체명", 230, "w"),
                                    ("bizno", "사업자등록번호", 120, "center"),
                                    ("yeo", "예정신고", 64, "center")):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col == "name"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── ② 조회 조건 ──
        c2 = self._card(left, "② 조회 조건")
        c2.pack(fill="x", pady=(12, 0))
        row = tk.Frame(c2, bg=CARD)
        row.pack(fill="x", padx=16, pady=(2, 8))
        tk.Label(row, text="과세기간", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        e = tk.Entry(row, textvariable=self.var_year, width=6, font=(FONT, 11),
                     relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=ACCENT,
                     justify="center")
        e.pack(side="left", padx=(8, 2), ipady=5)
        tk.Label(row, text="년", bg=CARD, fg=INK, font=(FONT, 10)).pack(side="left")
        Segmented(row, self.var_term, [("1", "1기"), ("2", "2기")], CARD,
                  width=110, height=32).pack(side="left", padx=(10, 0))
        Segmented(row, self.var_season, [("확정", "확정"), ("예정", "예정")], CARD,
                  width=110, height=32).pack(side="left", padx=(8, 0))
        row3 = tk.Frame(c2, bg=CARD)
        row3.pack(fill="x", padx=16, pady=(0, 4))
        Toggle(row3, self.var_summary, CARD).pack(side="left")
        tk.Label(row3, text="판매대행 엑셀 — 상호별 정리본 만들기 (Sheet1 정리·Sheet2 원본)",
                 bg=CARD, fg=INK, font=(FONT, 9)).pack(side="left", padx=(8, 0))
        tk.Label(c2, text="※ 확정신고: O·1 업체는 확정만, X·0 업체는 예정+확정으로 조회\n"
                          "※ 예정신고: O·1 업체만 '예정'으로 조회, X·0 업체는 건너뜀",
                 bg=CARD, fg=MUTE, font=(FONT, 8), justify="left"
                 ).pack(anchor="w", padx=16, pady=(0, 12))

        # ── 앱 모드 전환: 자료 출력 | 납부서 출력 ──
        modebar = tk.Frame(right, bg=BG)
        modebar.pack(fill="x", pady=(0, 10))
        Segmented(modebar, self.var_app_mode,
                  [("data", "자료 출력"), ("slip", "납부서 출력")],
                  BG, width=230, height=34).pack(side="left")
        tk.Label(modebar, text="납부서: 신고내역 조회에서 업체별 납부서 PDF 저장",
                 bg=BG, fg=MUTE, font=(FONT, 8)).pack(side="left", padx=(10, 0))

        # ── ③ 작업 선택 ──
        c3 = self._card(right, "③ 작업 선택")
        c3.pack(fill="x")
        self._c3 = c3
        for mod in ALL_PHASES:
            var = tk.BooleanVar(value=True)
            self._phase_vars[mod.KEY] = var
            r = tk.Frame(c3, bg=CARD)
            r.pack(fill="x", padx=16, pady=3)
            Toggle(r, var, CARD, command=self._refresh_validation).pack(side="left")
            tk.Label(r, text=mod.LABEL, bg=CARD, fg=INK,
                     font=(FONT, 10)).pack(side="left", padx=(10, 0))
            pill = Pill(r, CARD)
            pill.pack(side="right")
            self._phase_pills[mod.KEY] = pill
        tk.Frame(c3, bg=CARD, height=10).pack()

        # ── ④ 서류 처리 ──
        c4 = self._card(right, "④ 서류 처리")
        c4.pack(fill="x", pady=(12, 0))
        self._c4 = c4
        r1 = tk.Frame(c4, bg=CARD)
        r1.pack(fill="x", padx=16, pady=(2, 4))
        Segmented(r1, self.var_mode, [("print", "인쇄"), ("pdf", "PDF 저장")],
                  CARD, width=180, height=32).pack(side="left")
        tk.Label(c4, text="※ 저장 폴더에 PDF·판매대행 엑셀·결과 엑셀이 저장됩니다."
                          " (인쇄 모드여도 신용카드 작업을 켜면 폴더 필요)",
                 bg=CARD, fg=MUTE, font=(FONT, 8), justify="left"
                 ).pack(anchor="w", padx=16, pady=(0, 6))
        r2 = tk.Frame(c4, bg=CARD)
        r2.pack(fill="x", padx=16, pady=(0, 12))
        tk.Label(r2, text="저장 폴더", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        tk.Entry(r2, textvariable=self.var_outdir, font=(FONT, 9), relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True,
                                             padx=(8, 6), ipady=5)
        RButton(r2, "찾아보기", self._pick_outdir, kind="mini", bg=CARD,
                width=76, height=30, font=(FONT, 9, "bold")).pack(side="left")

        # ── 납부서 출력 카드 (slip 모드에서만 표시 — _apply_app_mode) ──
        c5 = self._card(right, "납부서 출력 설정")
        self._c5 = c5

        def _slip_entry(parent, var, width=None, mono=False):
            e = tk.Entry(parent, textvariable=var, font=((MONO if mono else FONT), 9),
                         relief="flat", highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT)
            if width:
                e.configure(width=width)
            return e

        s1 = tk.Frame(c5, bg=CARD)
        s1.pack(fill="x", padx=16, pady=(2, 4))
        tk.Label(s1, text="납부기한", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        _slip_entry(s1, self.var_due_date, width=12).pack(side="left",
                                                          padx=(8, 4), ipady=4)
        tk.Label(s1, text="예: 2026-07-27", bg=CARD, fg=MUTE,
                 font=(FONT, 8)).pack(side="left", padx=(0, 14))
        tk.Label(s1, text="날짜 형식", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        _slip_entry(s1, self.var_due_format, width=10, mono=True).pack(
            side="left", padx=(8, 4), ipady=4)
        tk.Label(s1, text="YYYY/YY/MM/DD 조합 (YY.MM.DD → 26.07.27)", bg=CARD,
                 fg=MUTE, font=(FONT, 8)).pack(side="left")

        s2 = tk.Frame(c5, bg=CARD)
        s2.pack(fill="x", padx=16, pady=(4, 2))
        tk.Label(s2, text="파일명", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        _slip_entry(s2, self.var_slip_template, mono=True).pack(
            side="left", fill="x", expand=True, padx=(8, 0), ipady=4)
        tk.Label(c5, text="{업체명}, {납부기한} 자리에 값이 들어가고 나머지는 그대로",
                 bg=CARD, fg=MUTE, font=(FONT, 8)).pack(anchor="w", padx=16)
        self.lbl_slip_preview = tk.Label(c5, text="", bg=CARD, fg=ACCENT,
                                         font=(FONT, 9, "bold"), anchor="w")
        self.lbl_slip_preview.pack(fill="x", padx=16, pady=(2, 6))

        s3 = tk.Frame(c5, bg=CARD)
        s3.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(s3, text="조회기간", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        Segmented(s3, self.var_slip_period,
                  [("1주", "1주"), ("1개월", "1개월"),
                   ("3개월", "3개월"), ("6개월", "6개월")],
                  CARD, width=230, height=30).pack(side="left", padx=(8, 0))
        srow = tk.Frame(c5, bg=CARD)
        srow.pack(fill="x", padx=16, pady=3)
        tk.Label(srow, text="납부서 출력 (최신 신고내역 1건, PDF 저장)",
                 bg=CARD, fg=INK, font=(FONT, 10)).pack(side="left")
        slip_pill = Pill(srow, CARD)
        slip_pill.pack(side="right")
        self._phase_pills["payment_slip"] = slip_pill

        tk.Label(c5, text="※ 실행 중에는 다른 프로그램 사용을 잠시 멈춰주세요 — 저장 창이 뜨는\n"
                          "   순간 세무사랑 등이 화면을 잡고 있으면 '인쇄 실패'가 날 수 있습니다 (자동 재시도 1회)",
                 bg=CARD, fg=MUTE, font=(FONT, 8), justify="left"
                 ).pack(anchor="w", padx=16, pady=(0, 2))
        s4 = tk.Frame(c5, bg=CARD)
        s4.pack(fill="x", padx=16, pady=(4, 12))
        tk.Label(s4, text="저장 폴더", bg=CARD, fg=MUTE,
                 font=(FONT, 9)).pack(side="left")
        _slip_entry(s4, self.var_outdir).pack(side="left", fill="x", expand=True,
                                              padx=(8, 6), ipady=5)
        RButton(s4, "찾아보기", self._pick_outdir, kind="mini", bg=CARD,
                width=76, height=30, font=(FONT, 9, "bold")).pack(side="left")

        # ── 실행 바 ──
        runbar = tk.Frame(right, bg=BG)
        runbar.pack(fill="x", pady=(12, 0))
        self._runbar = runbar
        self.btn_start = RButton(runbar, "시작", self._start, bg=BG,
                                 width=150, height=46)
        self.btn_start.pack(side="left")
        self.btn_stop = RButton(runbar, "중지", self._stop_clicked, kind="ghost",
                                bg=BG, width=96, height=46)
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_stop.set_enabled(False)
        self.lbl_status = tk.Label(runbar, text="대기 중", bg=BG, fg=MUTE,
                                   font=(FONT, 10))
        self.lbl_status.pack(side="left", padx=(14, 0))
        self.lbl_timer = tk.Label(runbar, text="", bg=BG, fg=ACCENT,
                                  font=(FONT, 10, "bold"))
        self.lbl_timer.pack(side="right", padx=(0, 4))
        self._run_started: float | None = None
        self._logfile = None   # 실행별 원본 로그 파일 (_start에서 설정)

        # ── 로그 콘솔 ──
        logwrap = tk.Frame(right, bg=CONSOLE_BG)
        logwrap.pack(fill="both", expand=True, pady=(12, 0))
        self.txt_log = tk.Text(logwrap, bg=CONSOLE_BG, fg=CONSOLE_FG,
                               font=(FONT, 9), relief="flat", state="disabled",
                               padx=12, pady=8, wrap="none")
        lsb = ttk.Scrollbar(logwrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=lsb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # 로그 색상 태그 — 업체 헤더(제목)는 일반 텍스트와 구분되는 단일 색
        self.txt_log.tag_configure("client", foreground="#C4B5FD",
                                   font=(FONT, 10, "bold"))
        self.txt_log.tag_configure("ok", foreground="#4ADE80")
        self.txt_log.tag_configure("warn", foreground="#FBBF24")
        self.txt_log.tag_configure("error", foreground="#F87171")
        self.txt_log.tag_configure("detail", foreground="#94A3B8")
        self.txt_log.tag_configure("phase", foreground="#E2E8F0",
                                   font=(FONT, 9, "bold"))
        self.txt_log.tag_configure("info", foreground=CONSOLE_FG)

        self._refresh_validation()

    # ── 업체 명부 ──
    def _import_excel(self):
        path = filedialog.askopenfilename(
            title="업체 명부 엑셀/CSV 선택",
            filetypes=[("엑셀/CSV", "*.xlsx *.xls *.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            rows = roster.import_table(path)
        except Exception as e:
            messagebox.showerror("가져오기 실패", f"파일을 읽지 못했습니다:\n{e}")
            return
        if not rows:
            messagebox.showwarning(
                "가져오기", "업체명·사업자등록번호를 찾지 못했습니다.\n"
                "열 제목(업체명/사업자등록번호)이 있는지 확인해주세요.")
            return
        self.clients = rows
        roster.save_clients(rows)
        self._refresh_clients()
        self._append_log(f"[v] 업체 명부 가져오기 — {len(rows)}곳 등록")

    def _refresh_clients(self):
        self.tree.delete(*self.tree.get_children())
        for c in self.clients:
            yeo = {True: "O", False: "X"}.get(c.get("yeojung"), "")
            self.tree.insert("", "end", iid=c["bizno"],
                             values=(c["name"], fmt_bizno(c["bizno"]), yeo))
        self.lbl_count.config(text=f"{len(self.clients)}곳")
        self._refresh_validation()
        self._refresh_slip_preview()

    def _add_client_inline(self):
        """명부 카드 상단 인라인 입력줄로 업체 1곳 추가."""
        name = self.v_add_name.get().strip()
        bizno = norm_regno(self.v_add_bizno.get())
        if not name or not bizno:
            messagebox.showwarning(
                "입력 확인", "업체명과 사업자등록번호(10자리)를 확인해주세요.")
            return
        if any(c["bizno"] == bizno for c in self.clients):
            messagebox.showwarning("중복", "이미 명부에 있는 사업자번호입니다.")
            return
        self.clients.append({"name": name, "bizno": bizno,
                             "yeojung": self.v_add_yeo.get() == "O"})
        roster.save_clients(self.clients)
        self._refresh_clients()
        self._append_log(f"[v] 업체 추가: {name} ({fmt_bizno(bizno)})")
        self.v_add_name.set("")
        self.v_add_bizno.set("")

    def _delete_selected(self):
        sel = set(self.tree.selection())   # 행 클릭으로 선택된 업체들
        if not sel:
            messagebox.showinfo("선택 삭제", "목록에서 삭제할 행을 먼저 클릭하세요.")
            return
        names = [c["name"] for c in self.clients if c["bizno"] in sel]
        if not messagebox.askyesno(
                "선택 삭제", f"{len(sel)}곳을 명부에서 삭제할까요?\n"
                + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")):
            return
        self.clients = [c for c in self.clients if c["bizno"] not in sel]
        roster.save_clients(self.clients)
        self._refresh_clients()
        self._append_log(f"[v] 업체 {len(sel)}곳 삭제")

    def _delete_all(self):
        if not self.clients:
            return
        if not messagebox.askyesno("전체 삭제",
                                   f"업체 명부 {len(self.clients)}곳을 모두 삭제할까요?"):
            return
        self.clients = []
        roster.save_clients([])
        self._refresh_clients()
        self._append_log("[v] 업체 명부 전체 삭제")

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="저장 폴더 선택")
        if d:
            self.var_outdir.set(d)
            self._refresh_validation()

    # ── 앱 모드 (자료 출력 / 납부서 출력) ──
    def _apply_app_mode(self):
        slip = self.var_app_mode.get() == "slip"
        if slip:
            self._c3.pack_forget()
            self._c4.pack_forget()
            self._c5.pack(fill="x", before=self._runbar)
        else:
            self._c5.pack_forget()
            self._c3.pack(fill="x", before=self._runbar)
            self._c4.pack(fill="x", pady=(12, 0), before=self._runbar)
        self._refresh_validation()

    def _refresh_slip_preview(self):
        if not hasattr(self, "lbl_slip_preview"):
            return
        sample = self.clients[0]["name"] if self.clients else "업체명"
        try:
            preview = render_slip_name(
                self.var_slip_template.get(), sample,
                self.var_due_date.get(), self.var_due_format.get())
        except Exception:
            preview = ""
        txt = f"미리보기: {preview}.pdf"
        if (self.var_due_date.get().strip()
                and parse_due_date(self.var_due_date.get()) is None):
            txt += "   ⚠ 납부기한 형식 확인 (예: 2026-07-27)"
        self.lbl_slip_preview.config(text=txt)

    # ── 실행 ──
    def _refresh_validation(self):
        if self.var_app_mode.get() == "slip":
            # 납부서 모드: 명부 + 저장 폴더 + 유효한 납부기한 필요
            ready = (self._browsers_ready and not self._busy
                     and bool(self.clients)
                     and bool(self.var_outdir.get().strip())
                     and parse_due_date(self.var_due_date.get()) is not None)
            self.btn_start.set_enabled(ready)
            return
        # 저장 폴더: PDF 모드이거나, 신용카드 phase(판매대행 엑셀 저장)가 켜져 있으면 필수
        need_dir = (self.var_mode.get() == "pdf"
                    or (self._phase_vars.get("card_sales") is not None
                        and self._phase_vars["card_sales"].get()))
        ready = (self._browsers_ready and not self._busy
                 and bool(self.clients)
                 and any(v.get() for k, v in self._phase_vars.items()
                         if k != "payment_slip")
                 and (not need_dir or bool(self.var_outdir.get().strip())))
        self.btn_start.set_enabled(ready)

    def _ensure_session(self):
        if self.session_loop is not None:
            return
        self.session_loop = asyncio.new_event_loop()
        threading.Thread(target=self.session_loop.run_forever, daemon=True).start()
        self.session = BrowserSession()

    def _gather_inputs(self) -> Inputs:
        return Inputs(
            year=self.var_year.get().strip(),
            term=self.var_term.get(),
            season=self.var_season.get(),
            excel_summary=self.var_summary.get(),
            output_dir=self.var_outdir.get().strip(),
            output_mode=self.var_mode.get(),
            due_date=self.var_due_date.get().strip(),
            due_format=self.var_due_format.get().strip() or "YY.MM.DD",
            slip_template=self.var_slip_template.get().strip(),
            slip_period=self.var_slip_period.get(),
        )

    def _save_settings(self):
        save_settings({
            "year": self.var_year.get().strip(),
            "term": self.var_term.get(),
            "season": self.var_season.get(),
            "excel_summary": self.var_summary.get(),
            "output_dir": self.var_outdir.get().strip(),
            "due_date": self.var_due_date.get().strip(),
            "due_format": self.var_due_format.get().strip() or "YY.MM.DD",
            "slip_template": self.var_slip_template.get().strip(),
            "slip_period": self.var_slip_period.get(),
        })

    def _start(self):
        if self._busy:
            return
        slip_mode = self.var_app_mode.get() == "slip"
        clients_sel = list(self.clients)   # 명부에 있는 업체는 전부 실행 대상
        if slip_mode:
            # 납부서 모드 — 신고시즌·예정신고 여부와 무관, 납부기한·템플릿만 검증
            if parse_due_date(self.var_due_date.get()) is None:
                messagebox.showwarning(
                    "입력 확인", "납부기한을 확인해주세요. 예: 2026-07-27")
                return
            bad = unknown_template_tokens(self.var_slip_template.get())
            if bad:
                messagebox.showwarning(
                    "파일명 템플릿",
                    "알 수 없는 항목이 있습니다: "
                    + ", ".join("{%s}" % t for t in bad)
                    + "\n사용 가능한 항목: {업체명}, {납부기한}")
                return
            selected = ["payment_slip"]
        else:
            year = self.var_year.get().strip()
            if not (year.isdigit() and len(year) == 4):
                messagebox.showwarning("입력 확인", "과세기간 연도(4자리)를 확인해주세요.")
                return
            # 신고구분은 업체별 예정신고 여부로 결정 — 없는 업체가 있으면 시작 불가
            missing = [c["name"] for c in clients_sel if c.get("yeojung") is None]
            if missing:
                head = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
                messagebox.showwarning(
                    "예정신고 여부 필요",
                    f"예정신고 여부가 없는 업체가 있습니다:\n{head}\n\n"
                    "명부 엑셀에 '예정신고' 열(O/X 또는 1/0)을 채워 다시 가져오거나,\n"
                    "'직접 추가'로 등록해주세요.")
                return
            selected = [k for k, v in self._phase_vars.items() if v.get()]
        inp = self._gather_inputs()
        if slip_mode:
            inp.output_mode = "pdf"   # 납부서는 항상 PDF 저장 (인쇄 없음)
        self._save_settings()

        self._busy = True
        self._stop = False
        self._run_started = time.time()
        self.lbl_timer.config(text="⏱ 00:00")
        logdir = app_data_dir() / "logs"
        logdir.mkdir(exist_ok=True)
        self._logfile = logdir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"
        self.btn_stop.set_enabled(True)
        self._refresh_validation()
        for pill in self._phase_pills.values():
            pill.set("idle")
        if slip_mode:
            self._append_log(
                f"[i] 시작 — 납부서 출력: 업체 {len(clients_sel)}곳 "
                f"/ 납부기한 {inp.due_date} / 조회기간 {inp.slip_period}")
        else:
            self._append_log(
                f"[i] 시작 — 업체 {len(clients_sel)}곳 × 작업 {len(selected)}종 "
                f"/ {inp.year}년 {inp.term}기 {inp.season}신고"
                f" · 신고구분은 업체별 예정신고 여부 적용")

        self._ensure_session()

        def emit(kind, **kw):
            self.events.put({"kind": kind, **kw})

        async def _runner():
            # 중지 버튼이 task를 즉시 cancel한다 — 진행 중이던 대기/조회가 그 자리에서
            # 끊기므로 여기서 잡아 GUI 상태를 정리(done)해 준다.
            try:
                await run_all(self.session, clients_sel, selected, inp, emit,
                              stop_check=lambda: self._stop)
            except asyncio.CancelledError:
                emit("log", text="[!] 즉시 중단됨 — 진행 중이던 작업을 끊었습니다. "
                                 "(브라우저는 유지, 다시 시작 가능)")
                emit("done", results=[])

        self._run_fut = asyncio.run_coroutine_threadsafe(_runner(), self.session_loop)

    def _stop_clicked(self):
        if not self._busy:
            return
        self._stop = True
        if self._run_fut is not None:
            self._run_fut.cancel()   # 진행 중인 단계까지 즉시 중단 (사용자 요청)
        self._append_log("[i] 중지 — 즉시 중단합니다.")

    # ── 이벤트 처리 ──
    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev.get("kind")
                if kind == "log":
                    self._append_log(ev.get("text", ""))
                elif kind == "status":
                    self.lbl_status.config(text=ev.get("text", ""))
                elif kind == "client":
                    self.lbl_status.config(
                        text=f"[{ev.get('index')}/{ev.get('total')}] {ev.get('name')}")
                    for pill in self._phase_pills.values():
                        pill.set("idle")
                elif kind == "phase":
                    pill = self._phase_pills.get(ev.get("key"))
                    if pill:
                        pill.set(ev.get("status", "idle"))
                elif kind == "done":
                    self._busy = False
                    self._run_fut = None
                    self.btn_stop.set_enabled(False)
                    self.lbl_status.config(text="완료 — 대기 중")
                    if self._run_started is not None:
                        total = self._fmt_elapsed(time.time() - self._run_started)
                        self.lbl_timer.config(text=f"⏱ {total} (총)")
                        self._append_log(f"[i] 총 소요 시간: {total}")
                        self._run_started = None
                    self._refresh_validation()
        except queue.Empty:
            pass
        if self._busy and self._run_started is not None:
            self.lbl_timer.config(
                text=f"⏱ {self._fmt_elapsed(time.time() - self._run_started)}")
        self.root.after(100, self._poll)

    @staticmethod
    def _fmt_elapsed(sec: float) -> str:
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # 내부 동작 로그 — 화면에는 숨기고 로그 파일에만 기록 (진단용)
    HIDE_LOG = (
        "PDF: 다이얼로그", "PDF: set 시도", "PDF: WM_COMMAND", "PDF: BM_CLICK",
        "PDF: 덮어쓰기", "PDF: [!]",
        "뷰어 인쇄 버튼 클릭", "인쇄방식 패널",
        "JS 클릭 사용", "폴백 사용",
        "기존 파일 삭제 후 재저장",
        "수임사업자 전환 완료", "수임사업자 이미 선택됨", "현재 조회되는 사업자는",
        "번호 입력이 지워짐", "검색 필터 미적용", "행 선택 (번호 일치)",
        "화면 준비 안 됨", "결과 변화 미감지", "자동 인쇄 미감지",
        "엑셀 중 대화상자", "엑셀: 실제",
    )

    def _write_logfile(self, line: str):
        """원본 로그(내부 동작 포함)를 실행별 파일에 기록 — 문제 진단용."""
        if self._logfile is None:
            return
        try:
            with open(self._logfile, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S  ") + line + "\n")
        except Exception:
            pass

    def _append_log(self, text: str):
        """화면 로그 — 진행상황과 결과만 사용자 친화적으로 표시.

        automation의 개발자식 로그([i]/[v]/[!], ━ 구분선)를 변환하고,
        내부 동작(HIDE_LOG)은 화면에서 숨긴다. 원본은 항상 로그 파일에 남는다.
        """
        raw = text or ""
        self._write_logfile(raw)
        if any(k in raw for k in self.HIDE_LOG):
            return
        t = raw
        tag = "info"
        if "━━━━" in t:                                   # 업체 시작 구분선
            tag = "client"
            t = "\n● " + t.replace("[i]", "").replace("━", "").strip()
        elif "저장 완료 (" in t:                          # PDF/엑셀 저장 결과
            tag = "ok"
            name = t.split("저장 완료 (", 1)[1].rstrip(") ")
            t = "      💾 " + name
        elif t.startswith("[!]"):
            tag = "error"
            t = "  ⚠ " + t[3:].strip()
        elif t.startswith("[v]"):
            # 성공이어도 조회권한 없음(출력 생략)은 진짜 성공과 구분되게 노란색
            tag = "warn" if "조회권한 없음" in t else "ok"
            t = "  ✓ " + t[3:].strip()
        elif t.startswith("[i] ──"):                      # phase 시작
            tag = "phase"
            t = "  ▷ " + t.replace("[i]", "").replace("─", "").replace("시작", "").strip()
        elif t.startswith("[i]"):
            t = "  " + t[3:].strip()
        elif t.startswith("    "):                        # 세부 진행 내역
            tag = "detail"
            t = "      · " + t.strip()
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", t + "\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _on_close(self):
        self._stop = True
        self._save_settings()
        if self._run_fut is not None:
            try:
                self._run_fut.cancel()
            except Exception:
                pass
        if self.session_loop is not None and self.session is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self.session.close(), self.session_loop)
                fut.result(timeout=5)
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
