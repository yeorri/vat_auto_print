"""파이프라인 — 브라우저 세션 유지 + 업체 loop × 선택 phase loop.

브라우저는 BrowserSession으로 GUI 수명 동안 유지된다: 첫 실행에서 launch+로그인하고,
실행이 끝나도 닫지 않아 다음 실행을 로그인 없이 이어서 처리할 수 있다.
phase는 서로 독립 — 한쪽 실패가 다음 phase/다음 업체를 막지 않는다(계속 진행, 결과만 기록).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright

from . import browser as B
from .phases import ALL_PHASES, PHASE_BY_KEY
from .phases.base import Inputs, PhaseResult

Emit = Callable[..., None]


def ordered_selected(selected_keys: list[str]):
    """선택된 key를 기본 순서(ALL_PHASES)대로 정렬해 phase 모듈 리스트 반환.

    ALL_PHASES 밖의 별도 모드 phase(납부서 출력 등)는 뒤에 이어 붙인다.
    """
    sel = set(selected_keys)
    mods = [p for p in ALL_PHASES if p.KEY in sel]
    known = {m.KEY for m in mods}
    mods += [PHASE_BY_KEY[k] for k in selected_keys
             if k in PHASE_BY_KEY and k not in known]
    return mods


async def wait_login(page, emit: Emit,
                     stop_check: Callable[[], bool] | None = None,
                     timeout: int = 600) -> bool:
    """홈택스 로그인 대기 (2초 polling, '로그아웃' 텍스트 감지).

    이미 로그인된 세션(프로필 유지)은 첫 폴링에서 즉시 통과한다.
    """
    def log(m):
        emit("log", text=m)

    emit("status", text="로그인 확인 중…")
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    notified = False
    while loop.time() < deadline:
        if stop_check and stop_check():
            log("[i] 로그인 대기 중단됨.")
            return False
        try:
            body = await page.locator("body").inner_text(timeout=2500)
        except Exception:
            body = ""
        if "로그아웃" in body:
            log("[v] 홈택스 로그인 확인 — 자동 진행 시작")
            emit("status", text="실행 중…")
            await asyncio.sleep(0.5)   # 로그인 직후 세션 안정화 여유 (첫 진입 실패 완화)
            return True
        if not notified:
            log(f"[i] 로그인 대기 — 홈택스에 세무대리인으로 로그인하세요. "
                f"(최대 {timeout // 60}분, 자동 감지)")
            notified = True
        await asyncio.sleep(2)
    log("[!] 로그인 미완료 — 시작하지 않습니다.")
    return False


class BrowserSession:
    """GUI 수명 동안 유지되는 브라우저 세션.

    - ensure(): (필요 시) launch + 홈택스 탭 확보 + 로그인 대기
    - 실행 사이에 브라우저를 닫지 않아 재로그인이 필요 없다
    - 서류 처리 모드(pdf/print)가 바뀌면 프린터 sticky 재설정을 위해 재시작
    """

    def __init__(self):
        self.pw = None
        self.ctx = None
        self.dialog_msgs: list = []
        self.output_mode: str | None = None

    def _alive(self) -> bool:
        if self.ctx is None:
            return False
        try:
            return bool(self.ctx.pages)  # 사용자가 창을 닫았으면 False/예외
        except Exception:
            return False

    async def ensure(self, output_mode: str, emit: Emit,
                     stop_check: Callable[[], bool] | None = None):
        """브라우저·탭·로그인 준비. 성공 시 홈택스 Page, 실패/중단 시 None."""
        def log(m):
            emit("log", text=m)

        if self._alive() and self.output_mode != output_mode:
            log("[i] 서류 처리 모드 변경 감지 — 프린터 설정을 위해 브라우저를 재시작합니다.")
            await self.close()

        if not self._alive():
            # 브라우저 시작 구간은 취소로부터 보호 — 도중에 끊기면
            # 프로세스가 고아로 남아 프로필 잠금이 걸릴 수 있다.
            await asyncio.shield(self._startup(output_mode, log))

        page = await B.open_hometax(self.ctx, log=log)
        if not await wait_login(page, emit, stop_check):
            return None
        return page

    async def _startup(self, output_mode: str, log) -> None:
        """브라우저 실행 — 잔재 정리 + sticky 프린터 설정 + launch."""
        await self.close()
        # 인쇄 대상(sticky)은 launch 전에만 적용 가능 — 모드에 맞게 설정
        if output_mode == "pdf":
            B.ensure_pdf_sticky_settings()
        else:
            prn = B.default_printer_name()
            if prn:
                B.ensure_sticky_printer(prn)
                log(f"[i] 인쇄 대상: 기본 프린터 '{prn}'")
            else:
                log("[!] 기본 프린터 조회 실패 — 이전 인쇄 대상이 그대로 사용될 수 있음")
        self.pw = await async_playwright().start()
        self.ctx = await B.launch(self.pw)
        await B.setup_context(self.ctx, self.dialog_msgs)
        self.output_mode = output_mode
        log("[i] Chromium 실행됨.")

    async def close(self):
        try:
            if self.ctx:
                await self.ctx.close()
        except Exception:
            pass
        try:
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass
        self.ctx = None
        self.pw = None
        self.output_mode = None


async def run_all(session: BrowserSession, clients: list[dict],
                  selected_keys: list[str], inp: Inputs, emit: Emit,
                  stop_check: Callable[[], bool] | None = None
                  ) -> list[PhaseResult]:
    """세션 브라우저에서 업체별 × 선택 phase 실행. 끝나도 브라우저는 유지."""
    def log(msg: str):
        emit("log", text=msg)

    phases = ordered_selected(selected_keys)
    results: list[PhaseResult] = []
    if not phases or not clients:
        log("[!] 실행할 작업 또는 업체가 없습니다.")
        emit("done", results=results)
        return results

    if inp.output_dir:
        Path(inp.output_dir).mkdir(parents=True, exist_ok=True)

    page = await session.ensure(inp.output_mode, emit, stop_check)
    if page is None:
        emit("done", results=results)
        return results

    ctx = session.ctx
    stopped = False
    for ci, client in enumerate(clients, 1):
        if stopped or (stop_check and stop_check()):
            break
        emit("client", index=ci, total=len(clients), name=client.get("name", ""))
        log(f"[i] ━━━━ [{ci}/{len(clients)}] {client.get('name')} "
            f"({client.get('bizno')}) ━━━━")

        # 예정신고기간엔 예정신고 대상(O) 업체만 처리 — 나머지는 기록만 남기고 건너뜀
        # (납부서 출력 모드는 신고시즌과 무관 — 스킵 규칙 미적용)
        is_slip_mode = all(p.KEY == "payment_slip" for p in phases)
        if (not is_slip_mode and inp.season == "예정"
                and client.get("yeojung") is not True):
            log(f"[i] {client.get('name')}: 예정신고 대상 아님 — 건너뜁니다.")
            results.append(PhaseResult(
                "season_skip", "전체", client_name=client.get("name", ""),
                ok=False, skipped=True, reason="예정신고 대상 아님 (예정신고기간)"))
            continue
        for pi, mod in enumerate(phases):
            if stop_check and stop_check():
                log("[i] 중단됨.")
                stopped = True
                break
            emit("phase", key=mod.KEY, status="run")
            log(f"[i] ── {mod.LABEL} 시작 ──")
            try:
                res = await mod.run(ctx, client, inp, emit, session.dialog_msgs,
                                    stop_check=stop_check)
            except Exception as e:  # noqa: BLE001
                res = PhaseResult(mod.KEY, mod.LABEL,
                                  client_name=client.get("name", ""),
                                  ok=False, reason=f"예외: {e}")
                log(f"[!] {mod.LABEL} 예외: {e}")
            results.append(res)
            emit("phase", key=mod.KEY, status="ok" if res.ok else "fail")
            tail = f" / {res.reason}" if res.reason else ""
            log(f"[v] {mod.LABEL}: {'성공' if res.ok else '실패'}{tail}")

            if res.fatal:
                # 사업자번호 오류 등 — 이 업체의 남은 phase는 어차피 안 되므로 건너뜀
                log(f"[!] {client.get('name')}: 사업자번호 확인 필요 — "
                    "남은 작업을 건너뛰고 다음 업체로 넘어갑니다. (결과 엑셀에 기록)")
                for rest in phases[pi + 1:]:
                    results.append(PhaseResult(
                        rest.KEY, rest.LABEL, client_name=client.get("name", ""),
                        ok=False, skipped=True, reason="건너뜀 — 사업자등록번호 오류"))
                    emit("phase", key=rest.KEY, status="fail")
                break

    ok_n = sum(1 for r in results if r.ok)
    log(f"[i] 전체 종료 — 성공 {ok_n} / 시도 {len(results)}. "
        "브라우저는 유지됩니다. 다시 시작하면 로그인 없이 이어서 진행됩니다.")

    if results:
        try:
            from .report import write_results
            xlsx = write_results(results, clients, inp)
            log(f"[v] 결과 엑셀 저장: {xlsx}")
        except Exception as e:  # noqa: BLE001
            log(f"[!] 결과 엑셀 저장 실패: {e}")

    emit("done", results=results)
    return results
