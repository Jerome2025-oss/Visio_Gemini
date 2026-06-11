"""
Capture TradingView via Playwright — wrapper autour de ``src/capture.py``.

Dette technique : dépend de ``src/settings.CaptureJob`` (couplage legacy temporaire,
à découpler post-refonte).
"""

from modules.capture.service import capture

__all__ = ["capture"]
