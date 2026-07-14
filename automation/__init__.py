"""부가세 신고자료 자동 출력 — automation 패키지 공개 API."""
from .phases import ALL_PHASES, PHASE_BY_KEY
from .phases.base import Inputs, PhaseResult
from .pipeline import BrowserSession, run_all

__all__ = [
    "ALL_PHASES",
    "PHASE_BY_KEY",
    "Inputs",
    "PhaseResult",
    "BrowserSession",
    "run_all",
]
