import sys

def modify_exporter():
    filepath = 'src/core/exporter.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    search1 = """      'hydro1d': 一维推算水面线.csv
      'hydro1d_range': 一维推算水面线淹没范围坐标.csv"""
    replace1 = """      'hydro1d': 一维推算水面线.csv
      'hydro1d_range': 一维推算水面线淹没范围坐标.csv
      'hydro1d_table': 一维推算水力要素表.csv"""

    content = content.replace(search1, replace1)

    search2 = """            'hydro1d': True, 'hydro1d_range': True
        }"""
    replace2 = """            'hydro1d': True, 'hydro1d_range': True, 'hydro1d_table': True
        }"""

    content = content.replace(search2, replace2)

    search3 = """            if options.get('hydro1d_range', True):
                p8 = os.path.join(line_dir, f"{line.name}一维推算水面线淹没范围坐标.csv")
                export_hydro1d_range_csv(p8, line, cfg)
                written.append(p8)

    return written"""
    replace3 = """            if options.get('hydro1d_range', True):
                p8 = os.path.join(line_dir, f"{line.name}一维推算水面线淹没范围坐标.csv")
                export_hydro1d_range_csv(p8, line, cfg)
                written.append(p8)
            if options.get('hydro1d_table', True):
                p9 = os.path.join(line_dir, f"{line.name}一维推算水力要素表.csv")
                export_hydro1d_table_csv(p9, line, cfg.csv_encoding)
                written.append(p9)

    return written"""

    content = content.replace(search3, replace3)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

modify_exporter()
