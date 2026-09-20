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
open(r"E:\VSCODE\duanmianjisuan\_verify.txt", "w", encoding="utf-8").write(
    sys.version + "\n" + "\n".join(o))
