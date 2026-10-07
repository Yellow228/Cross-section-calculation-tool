import sys

def modify_hydro1d():
    filepath = 'src/core/hydro1d.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Search for def compute_hydro1d_profile(...)
    search1 = """def compute_hydro1d_profile(line: ProfileLine, cfg, results: dict = None,
                            warnings: list[str] = None) -> list[float]:"""
    replace1 = """def compute_hydro1d_profile(line: ProfileLine, cfg, results: dict = None,
                            warnings: list[str] = None) -> tuple[list[float], list[Optional[HydroNode]]]:"""

    content = content.replace(search1, replace1)

    search2 = """    if n < 2:
        # 断层面数量不足，无法推算，回退到原设计水位
        res = [float('nan')] * n
        for i, sec in enumerate(sections):
            res_data = results.get(sec.name)
            if res_data and not math.isnan(res_data.design_level):
                res[i] = res_data.design_level
        return res"""
    replace2 = """    if n < 2:
        # 断层面数量不足，无法推算，回退到原设计水位
        res = [float('nan')] * n
        for i, sec in enumerate(sections):
            res_data = results.get(sec.name)
            if res_data and not math.isnan(res_data.design_level):
                res[i] = res_data.design_level
        return res, [None] * n"""

    content = content.replace(search2, replace2)

    search3 = """    # 初始化返回数组
    res_levels = [float('nan')] * n"""
    replace3 = """    # 初始化返回数组
    res_levels = [float('nan')] * n
    res_nodes: list[Optional[HydroNode]] = [None] * n"""

    content = content.replace(search3, replace3)

    search4 = """    if ds_idx == 0:
        # 索引 0 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i]
                sec_down = sections[i-1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d: dict = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i-1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i+1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i+1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels[i] = Z_down
    else:
        # 索引 n-1 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i]
                sec_down = sections[i+1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i+1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i-1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i-1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels[i] = Z_down"""

    replace4 = """    def calc_and_record_nodes():
        for i in range(n):
            if math.isnan(res_levels[i]):
                continue
            z_min_sec = min(sections[i].z) + 0.01
            z_max_sec = max(sections[i].z) + 10.0
            node = get_node_state(sections[i], res_levels[i], Qs[i], dists[i], cfg, infos[i])
            node.Z_crit = compute_critical_depth(sections[i], Qs[i], z_min_sec, z_max_sec)
            res_nodes[i] = node

        # 根据流向计算水头损失
        # 水头损失(hl)计算：hl = H_upstream - H_downstream
        if ds_idx == 0:
            # 索引 0 是下游, 索引 n-1 是上游
            for i in range(n - 1): # i 从 0 到 n-2 (不包括最上游 n-1)
                # 计算当前节点 i 的水头损失，需要其相邻上游节点 i+1
                curr_node = res_nodes[i]
                up_node = res_nodes[i+1]
                if curr_node and up_node:
                    curr_node.hl = up_node.H - curr_node.H
        else:
            # 索引 n-1 是下游, 索引 0 是上游
            for i in range(1, n): # i 从 1 到 n-1 (不包括最上游 0)
                # 计算当前节点 i 的水头损失，需要其相邻上游节点 i-1
                curr_node = res_nodes[i]
                up_node = res_nodes[i-1]
                if curr_node and up_node:
                    curr_node.hl = up_node.H - curr_node.H

    if ds_idx == 0:
        # 索引 0 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i]
                sec_down = sections[i-1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d: dict = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i-1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i+1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i+1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels[i] = Z_down
    else:
        # 索引 n-1 是下游
        if regime == "subcritical":
            # 缓流：从下游推上游 (n-1 -> 0)
            res_levels[-1] = Z_initials[-1]
            for i in range(n - 2, -1, -1):
                sec_up = sections[i]
                sec_down = sections[i+1]
                z_min_up = min(sec_up.z) + 0.01
                z_max_up = max(sec_up.z) + 10.0

                d = {}
                Z_up = standard_step_method_subcritical(
                    sec_down, res_levels[i+1], Qs[i+1], dists[i+1],
                    sec_up, Qs[i], dists[i],
                    z_min_up, z_max_up, cfg,
                    info_down=infos[i+1], info_up=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_up, Qs[i], z_min_up, z_max_up)
                if Z_up < Z_crit:
                    d["crossed_critical"] = True
                    Z_up = Z_crit
                _note(i, d)
                res_levels[i] = Z_up
        else:
            # 急流：从上游推下游 (0 -> n-1)
            res_levels[0] = Z_initials[0]
            for i in range(1, n):
                sec_up = sections[i-1]
                sec_down = sections[i]
                z_min_down = min(sec_down.z) + 0.01
                z_max_down = max(sec_down.z) + 10.0

                d = {}
                Z_down = standard_step_method_supercritical(
                    sec_up, res_levels[i-1], Qs[i-1], dists[i-1],
                    sec_down, Qs[i], dists[i],
                    z_min_down, z_max_down, cfg,
                    info_up=infos[i-1], info_down=infos[i], diag=d
                )
                Z_crit = compute_critical_depth(sec_down, Qs[i], z_min_down, z_max_down)
                if Z_down > Z_crit:
                    d["crossed_critical"] = True
                    Z_down = Z_crit
                _note(i, d)
                res_levels[i] = Z_down

    calc_and_record_nodes()"""

    content = content.replace(search4, replace4)

    search5 = """    if warnings is not None:
        warnings.extend(_hydro1d_warnings(line, _diags))
    return res_levels"""
    replace5 = """    if warnings is not None:
        warnings.extend(_hydro1d_warnings(line, _diags))
    return res_levels, res_nodes"""

    content = content.replace(search5, replace5)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

modify_hydro1d()
