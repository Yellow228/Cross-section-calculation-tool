import sys

def modify_exporter():
    filepath = 'src/core/exporter.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    new_function = """def export_hydro1d_table_csv(path: str, line: ProfileLine, encoding: str = "utf-8-sig") -> None:
    \"\"\"一维推算水力要素表 CSV (HEC-RAS Profile Output Table format).\"\"\"
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "断面名称/里程桩号 (Station / River Station)",
            "计算水位 (Water Surface Elevation, Z)",
            "临界水深/水位 (Critical Water Surface, Z_crit)",
            "总流量 (Flow, Q)",
            "总过水断面积 (Area, A)",
            "平均流速 (Velocity, V)",
            "动能修正系数 (Velocity Weighting Coefficient, alpha)",
            "摩阻坡降 (Friction Slope, S_f)",
            "段间水头损失 (Head Loss, h_l)",
            "弗鲁德数 (Froude Number, Fr)"
        ])

        nodes = getattr(line, "hydro1d_nodes", [])

        def safe_fmt(val):
            if val is None or val != val: # None or NaN
                return ""
            return f"{val:.6f}"

        for i, sec in enumerate(line.sections):
            c = line.chainage[i] if line.chainage and i < len(line.chainage) else sec.s[0]
            name_station = f"{sec.name} / {c:.3f}"

            node = nodes[i] if i < len(nodes) else None
            if not node:
                w.writerow([name_station, "", "", "", "", "", "", "", "", ""])
                continue

            w.writerow([
                name_station,
                safe_fmt(node.Z),
                safe_fmt(node.Z_crit),
                safe_fmt(node.Q),
                safe_fmt(node.A),
                safe_fmt(node.V),
                safe_fmt(node.alpha),
                safe_fmt(node.Sf),
                safe_fmt(node.hl),
                safe_fmt(node.Fr)
            ])


"""

    # insert before export_section_names_xlsx
    search = """def export_section_names_xlsx(path: str, names: list[str]) -> None:"""
    content = content.replace(search, new_function + search)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

modify_exporter()
