import os
import sys

o = []
try:
    import numpy; o.append(f"numpy {numpy.__version__} OK")
except Exception as e:
    o.append(f"numpy FAIL {e}")
try:
    import openpyxl; o.append(f"openpyxl {openpyxl.__version__} OK")
except Exception as e:
    o.append(f"openpyxl FAIL {e}")
try:
    import matplotlib
    o.append(f"matplotlib {matplotlib.__version__} OK")
    matplotlib.use("QtAgg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    fig = plt.figure()
    FigureCanvasQTAgg(fig)
    o.append("matplotlib QtAgg backend OK (可嵌入 Qt)")
except Exception as e:
    o.append(f"matplotlib QtAgg FAIL {e}")
try:
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
    from PySide6.QtCore import qVersion
    import PySide6
    o.append(f"PySide6 {PySide6.__version__} OK (Qt {qVersion()})")
    o.append("  QtWidgets/QMainWindow/QVBoxLayout 均可导入")
except Exception as e:
    o.append(f"PySide6 FAIL {e}")
try:
    import PyInstaller; o.append(f"PyInstaller {PyInstaller.__version__} OK")
except Exception as e:
    o.append(f"PyInstaller FAIL {e}")
# 结果写到仓库根目录的 _verify.txt（被 .gitignore 的 `_*.txt` 挡住，不会入库）。
# ⚠ 这里原来写死了本机绝对路径（`<盘符>:\...\_verify.txt`）——
#   换台机器目录不同就会直接抛 FileNotFoundError。改成按脚本位置推算。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
text = sys.version + "\n" + "\n".join(o)
open(os.path.join(ROOT, "_verify.txt"), "w", encoding="utf-8").write(text)
print(text)
