import sys

def modify_dialogs():
    filepath = 'src/app/dialogs.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    search = """            'hydro1d': QCheckBox("一维推算水面线.csv (仅已启用的线)"),
            'hydro1d_range': QCheckBox("一维推算水面线淹没范围坐标.csv (仅已启用的线)"),"""

    replace = """            'hydro1d': QCheckBox("一维推算水面线.csv (仅已启用的线)"),
            'hydro1d_range': QCheckBox("一维推算水面线淹没范围坐标.csv (仅已启用的线)"),
            'hydro1d_table': QCheckBox("一维推算水力要素表.csv (仅已启用的线)"),"""

    content = content.replace(search, replace)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

modify_dialogs()
