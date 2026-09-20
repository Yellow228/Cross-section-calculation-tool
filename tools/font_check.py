"""用真实 Windows 平台插件（非 offscreen）验证界面中文能否正常显示。

只构造窗口并 grab 截图，**不调用 show()**，不会弹出任何可见窗口。
结果写入 _fontcheck.txt，并把主界面与两个面板的截图存到 output/_preview。

为什么需要它：
    Qt 的 offscreen 平台插件**不加载任何字体**（QFontDatabase 家族数为 0），
    在那上面截图，界面中文会全部渲染成方块。那是渲染环境的假象，不是程序问题——
    但这个假象很容易被误判成真 bug，所以单独用真实平台验一次。
    另外把 exe 发给同事时，若对方机器缺中文字体，也可用本脚本快速定位。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

out = []
try:
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from app.main import setup_font

    app = QApplication(sys.argv)
    fams = QFontDatabase.families()
    out.append(f"平台插件: {app.platformName()}")
    out.append(f"可选字体数: {len(fams)}")
    picked = setup_font(app)
    out.append(f"setup_font 选中: {picked}")

    from PySide6.QtGui import QFont, QRawFont
    for name in ("Microsoft YaHei", "SimHei", "SimSun"):
        out.append(f"  含 {name}: {name in fams}")

    # 检查选中的字体到底有没有中文字形
    f = QFont(picked or "Sans Serif", 9)
    rf = QRawFont.fromFont(f)
    ok_cjk = rf.supportsCharacter(ord("断"))
    ok_ascii = rf.supportsCharacter(ord("A"))
    out.append(f"字体实际族名: {rf.familyName()}")
    out.append(f"  支持 '断': {ok_cjk}   支持 'A': {ok_ascii}")

    from app.main_window import MainWindow
    win = MainWindow()
    win.resize(1500, 900)
    shots = os.path.join(ROOT, "output", "_preview")
    os.makedirs(shots, exist_ok=True)
    win.grab().save(os.path.join(shots, "0_主界面.png"))
    out.append("主界面截图已保存（未 show，无可见窗口）")
    for tag, dlg in (("0a_计算设置面板", win.dlg_settings), ("0b_批量填写面板", win.dlg_batch)):
        dlg.resize(dlg.sizeHint())
        dlg.grab().save(os.path.join(shots, f"{tag}.png"))
        out.append(f"{tag} 截图 OK")
    out.append("")
    out.append("结论: " + ("界面中文可正常显示" if ok_cjk else "字体缺中文字形！"))
except Exception:
    import traceback
    out.append("FAILED\n" + traceback.format_exc())

with open(os.path.join(ROOT, "_fontcheck.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out))
