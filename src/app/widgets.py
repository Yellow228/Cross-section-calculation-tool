"""界面通用小部件构造助手。"""

from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox


def make_spin(minimum: float, maximum: float, step: float, decimals: int,
              value: float | None = None, suffix: str | None = None) -> QDoubleSpinBox:
    """统一风格的数值输入框。

    keyboard_tracking 一律关闭：否则用户键入 "0.35" 的过程中，
    输到 "0.3" 就会触发一次全量重算，白算好几次。
    """
    w = QDoubleSpinBox()
    w.setRange(minimum, maximum)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setKeyboardTracking(False)
    if value is not None:
        w.setValue(value)
    if suffix is not None:
        w.setSuffix(suffix)
    return w
