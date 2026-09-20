# CLAUDE.md

面向 AI 编码助手的项目上下文。**改动代码前请先读完本文**，
它记录的每一条都是本项目已经真实踩过的坑，重复踩会直接产生错误结果或崩溃的 exe。

---

## 一、这是什么

把原 MATLAB 河道断面「水位–流量关系曲线」计算脚本（`原始计算代码/*.m`）
重写为 Python 桌面程序（PySide6 + matplotlib），并增加三类可视化。

**核心目标是与原 MATLAB 数值一致**，其次才是易用性和功能扩展。
因此存在一条铁律：

> 任何改动都先跑 `tests/test_core.py`，必须**全绿**。
> 涉及数值逻辑的改动，必须能说清它与 `原始计算代码/` 里的对应关系。

项目不是从零设计——`项目方案.md` 是完整的设计与决策记录，
含 10 个原实现缺陷（D1~D10）的处置结论、Q1~Q14 的用户决策记录、
以及每次返工的复盘。**改设计前先查那里有没有相关决策。**

---

## 二、三条硬约束

### 1. `src/core/` 保持零第三方依赖

只有 `reader.py`（读 xlsx）和 `exporter.py`（写 xlsx）允许 import openpyxl。
其余模块**不要引入 numpy / pandas**。

这不是洁癖：计算要用纯 Python 标量循环，因为**浮点累加顺序必须与 MATLAB 一致**，
向量化会改变求和顺序引入末位差异，直接破坏「与 MATLAB 逐列 diff < 1e-9」的回归闸门。
计算量只有 10⁵ 量级，不是瓶颈。

副作用（好事）：core 层不装任何依赖就能跑测试，调试成本极低。

### 2. 分区必须"共享转折点"，没有开关

分区写法**唯一正确**的是 `{1:i1, i1:i2, i2:n}`，即相邻分区共享转折点索引。

- ❌ **不存在** `Config.compat_zone_overlap`（已删除，不要再加回来）
- ❌ 不要改成"不重叠"的 `{1:i1, i1+1:i2, i2+1:n}`

原因见「坑 #1」。这条曾被写进方案文档当作"缺陷 D8"，害用户基于错误信息做了决策，
后来完整撤回。**如果你再次认为这是缺陷，先读 `terrain._build_zones` 的注释。**

> 若确实需要按人工判断改分区，正确做法是用界面「分区调节」面板
> （写 `Section.zone_manual` / `zone_left` / `zone_right`），
> **不要去改 `_build_zones` 的写法**。算法只管自动推算，人工干预走覆盖层。

### 3. 不要往 `build_exe.spec` 的 `excludes` 里加标准库模块

尤其别加 `unittest`。见「坑 #4」。

---

## 三、常用命令

全部用工作区 venv，不要用全局 Python、不要 `pip install -g`。

```bat
:: 单元测试（不需要任何第三方依赖，个数以实际输出为准）
.venv\Scripts\python.exe tests\test_core.py

:: 命令行批处理（不开界面，跑真实数据）
.venv\Scripts\python.exe tools\run_batch.py

:: 界面冒烟测试（离屏渲染三张图到 output\_preview，含大量断言）
.venv\Scripts\python.exe tools\gui_smoke.py

:: 启动界面
.venv\Scripts\python.exe src\app\main.py

:: 源码自检（离屏跑完整流程，退出码 0/1）
.venv\Scripts\python.exe src\app\main.py --selftest

:: 打包 → 产物在 dist\断面计算工具\
.venv\Scripts\python.exe -m PyInstaller build_exe.spec --noconfirm

:: 打包后必须跑这个：启动 exe 并等它跑完自检
.venv\Scripts\python.exe tools\check_exe.py
```

运行 Python 时若中文输出乱码，先设 `$env:PYTHONIOENCODING = "utf-8"`。

---

## 四、架构：改什么去哪找

### `src/core/` — 纯计算层

| 模块 | 职责 |
|---|---|
| `config.py` | **所有**可调参数的唯一来源（水位步长、阈值、编码、分组规则…） |
| `model.py` | `SectionParams` / `Section` / `TerrainInfo` / `SectionResult` / `ProfileLine` / `Project`；**`Section` 上的手动覆盖字段**（`thalweg_manual` / `zone_manual`+`zone_left`/`zone_right` / `disaster_idx_manual`）与 `override_errors()`、`thalweg_index()`；`argmin_index()` / `is_valid_index()` 供 terrain 共用 |
| `reader.py` | xlsx 解析、块切分、块分类、空间分组（唯一依赖 openpyxl 的模块之一） |
| `geom.py` | `section_geom`(A/P/B)、`surface_span`、**`wetted_polygons`**（见坑 #2） |
| `terrain.py` | 深泓 / 峰点 / 转折点两阶段扫描 / 成灾水位 / **`_build_zones`**（见坑 #1）；**先算自动值再套人工覆盖**（`_zone_override`、`_peaks_around`）；⚠ **转折点（永远自动）与分区边界（可覆盖）是两条独立的路**，见坑 #13 |
| `rating.py` | `matlab_colon` 水位向量 + 复式分区曼宁求和 + 单调性检查 + **`hvec_row_counts`**（见坑 #12） |
| `interp.py` | `interp1` 线性+外推、水面交点搜索、坐标插值 |
| `spatial.py` | 线段求交、折线最短距离、按相交关系分组（**纯标准库，不要引入 shapely**） |
| `chainage.py` | 里程计算、起始端点重定基、按交点反算桩号 |
| `slope.py` | 按纵断面推算平均比降。默认口径**约翰斯通-克罗斯法**，另有两端点法 / 最小二乘可切换。⚠ 两处易错：① 用 `profile.dist` **原始起点距**坐标系，不是 `line.chainage` 重定基桩号；② JC 法必须用**开方版**公式，线性版恒等于两端点法（见「坑 #10」） |
| `params.py` | 参数手工填写 / 批量修改 / 导入合并（`only_missing` 模式） |
| `solver.py` | 单断面求解编排 |
| `project_io.py` | 工程文件（`.dmprj`，单个 JSON 文本）读写。全量快照，**不存计算结果**。⚠ `null` 对不同字段含义不同，见「坑 #11」 |
| `exporter.py` | 三个 CSV + xlsx（另一个依赖 openpyxl 的模块） |

### `src/app/` — PySide6 界面层

| 模块 | 职责 |
|---|---|
| `main.py` | 入口；`--selftest` 自检；字体与图标设置 |
| `main_window.py` | 主窗口，菜单栏、列表、重算调度、导出；手动覆盖的写入与校验（`_try_apply_override`） |
| `dialogs.py` | 顶部菜单栏弹出的四个非模态面板：计算设置 / 批量填写 / 比降推算 / **分区调节 + 成灾水位**（后两个见 §五 #13） |
| `widgets.py` | `make_spin()` 等共用控件 |
| `param_panel.py` | 左侧「当前断面参数」精调面板 |
| `canvas_base.py` | matplotlib 画布基类 |
| `section_view.py` | 断面形态图；**图上拾取模式**（`begin_pick` / `picked` / `cancel_pick`，只吸附实测测点）；手动与自动的记号区分 |
| `rating_view.py` / `profile_view.py` | 水位–流量曲线 / 沿河纵剖面 |

### `tools/` — 辅助脚本（不是一次性脚本，都值得保留）

`run_batch` 批处理 · `gui_smoke` 界面冒烟 · `check_exe` exe 自检 ·
`check_csv_encoding` CSV 编码核验 · `check_icon` 图标核验 · `font_check` 字体核验 ·
`check_spatial` 分组核验 · `check_slope` 比降推算核对 ·
`check_overrides` 手动覆盖核对（含行数警告的端到端核验）· `verify_env` 依赖核验 ·
`peek_xlsx`/`scan_sections` 数据窥探 · `make_icon` 生成图标 · `wheel_list` 生成离线依赖清单

---

## 五、⚠️ 已踩过的坑（改代码前必读）

### #1 分区改成"不重叠"会漏算 19% 面积

**错误认知**：以为 `{1:i1, i1:i2, i2:n}` 里转折点被相邻分区重复计入，面积会多算。

**真相**：每个分区是**用自己的点序列独立调 `section_geom`** 算面积的。
共享一个"点"不会重复算面积——**点的宽度为零**。而改成不重叠后，
转折点两侧的线段 `(L, L+1)` 不属于任何分区，**整段消失**：

```
共享端点 [(0,2),(1,6),(5,7)]  覆盖线段 6/6  ✅
不重叠   [(0,2),(2,6),(6,7)]  覆盖线段 4/6  ❌ 缺 (1,2)(5,6)
```

真实影响：SJC4-1 少 19.0%、SJC4-2 少 24.3%、yqc6-6 少 6.3%。

**判据**：判断"是否重复计算"要按**线段**推演，不能只看索引集合有无交集。

**锁死它的测试**：`test_zones_cover_every_segment`（断言线段全覆盖）、
`test_zone_area_sum_equals_whole_section`（跨 19 个水位断言分区求和 == 整断面）。
后者最有力——任何线段漏算立刻红。

### #2 水位填充不能用 `fill_between` + `np.minimum`

`np.minimum(z, H)` 会把岸上地形**截断成水位高度**，填充下边界因此变成"截断后的地形"，
水面越过真实交点一路漫到最外侧采样点（看起来两岸都被淹了）。
`interpolate=True` **完全救不回来**。

必须用 `geom.wetted_polygons()`：先在跨越水位的线段上插入精确插值交点，
再把连续水下段闭合成多边形；水面不连续时返回**多个**多边形（江心洲会正确分两块）。

**注意**：这只影响绘图，A/P/B/Q 计算一直是对的（`section_geom` 本就逐段插值）。
但图是给人看的，画错会被评审质疑。

**锁死它的测试**：`test_area_matches_computed_wetted_area` —— 用**鞋带公式**算
填充多边形面积，与 `section_geom` 的过水面积比对，5 个水位吻合到 1e-9。
「画出来的面积必须等于算出来的面积」，这条同时锁住绘图与计算两条链路。

### #3 导出 CSV 必须 `utf-8-sig`（带 BOM）

中文 Windows 的 Excel / WPS 打开 CSV 的规则：
**有 BOM(EF BB BF) → UTF-8；无 BOM → 系统 ANSI(GBK)**。

写成无 BOM 的 UTF-8 会被当 GBK 解，`断面` → `鏂潰`、
`名称,平面坐标X` → `鍚嶇О,骞抽潰鍧愭爣X`。

`Config.csv_encoding` 默认 `utf-8-sig`，**不要改回 `utf-8`**。
界面「计算设置 → 导出」可切 utf-8 / gbk（下游平台不接受 BOM 时用）。

**核验**：`tools/check_csv_encoding.py` 按 Excel 的真实判定规则逐文件判定，
并打印 Excel 实际会看到的表头首行。
**锁死它的测试**：`test_csv_has_utf8_bom`（断言前三字节 + 默认配置值）。

### #4 `excludes` 里排 `unittest` 会让 exe 静默崩溃

`pyparsing.testing` 会 import `unittest`，而 matplotlib 的导入链是
`matplotlib → rcsetup → _fontconfig_pattern → pyparsing`，被它拖死。
因为 exe 是 `console=False`，**双击后什么都不显示**，用户只会以为程序坏了。

**教训：PyInstaller 报 "Build complete" 完全不可信。**
所以本项目所有 GUI exe 打包都必须配一个可自动执行的自检入口（`--selftest`），
打包后用 `tools/check_exe.py` 跑一遍。没有这一步 = 没验证。

### #5 `excludes` 之外：PyInstaller 会复用构建缓存

改了资源（如 .ico）直接重打，产物里还是旧的。
必须删掉 `build/` 和 `dist/` 再打。另外**后台跑 PyInstaller 可能静默失败**
（无输出、dist 保持旧时间戳），用前台 + 日志文件跑。

### #6 `.ico` 必须从 1024 主图保存

把 16×16 的帧当保存源调 `save(sizes=[...])`，结果 .ico 里**只有一档尺寸**，
但资源管理器照样能显示图标，肉眼完全看不出，只有放大才糊。
正确做法：从 1024 主图保存，让 Pillow 逐级降采样（现在 7 档）。
核验用 `tools/check_icon.py`（同时查 .ico 内含尺寸与 exe 的 PE 资源项数）。

### #7 offscreen 截图里的"中文方块"是假象

Qt 的 offscreen 平台插件**不加载字体库**（`QFontDatabase.families()` 返回 0），
所以离屏截图里中文全是方块。这不代表程序有问题。

涉及字体的验证要用 `tools/font_check.py`（走**真实 windows 平台**构造窗口 + `grab()`，
**不调用 `show()`**，不弹可见窗口）。程序启动时 `main.setup_font()` 已显式指定中文字体，
不赌系统默认。

**通用教训**：离屏平台的渲染/字体表现不能当作真实效果。

### #8 过滤目录别用子串匹配

`tools/check_csv_encoding.py` 最初写 `if "_selftest" in dp: continue`，
当扫描目标本身就是 `_selftest` 目录时，其下所有子目录（`_selftest\SJC4`）
路径都含 `_selftest` 而被误跳过，结果报"共 0 个 CSV"。
**按相对路径的分量判断**：`rel.split(os.sep)`。

### #9 空间分组优于编号分组，别改回去

横断面按**与纵断面的平面相交关系**分组（`spatial.assign_by_intersection`），
默认 `group_rule="spatial"`，容差 `intersection_tolerance=1.0` m。

理由：编号写错就分错组且发现不了；几何关系是事实。
而且**文件内块的顺序不可依赖**——老文件纵断面在末尾，新文件纵断面在每组开头，
所以"按出现顺序关联"根本不通用。

分组兜底：缺「纵断面」块时自动回退按编号前缀分组并告警。
里程直接用交点位置插值算（比"相邻深泓点距离累加"精确），且能自动排出沿河真实顺序。

### #10 约翰斯通-克罗斯法只有**开方版**是对的

教材公式（Johnstone & Cross, 1949）：

```
S = [ Σ L_i·√S_i / Σ L_i ]²
```

但网络资料（CSDN / ArcGIS 教程）多写成**线性加权**版 `S = ΣL_i·S_i / ΣL_i`。
**后者恒等于两端点法，不是另一种方法**：

```
Σ L_i·S_i = Σ L_i·(Δh_i / L_i) = Σ Δh_i = 总落差
```

实现时必须用开方版。物理理由：曼宁公式 `Q ∝ √S`，所以等效比降应对 √S 加权
（输水能力等效），而不是对 S 加权（那只是落差等效）。

**护栏**：√S 是凹函数，由 Jensen 不等式 `(E√S)² ≤ E[S]`，
故 **JC 恒 ≤ 两端点法**（各子段比降相等时取等）。测试里断言了这条，
若有人误改成线性加权会立刻失败。

倒坡子段（`S_i ≤ 0`）使 √S 无定义，**跳过并计数**（实测 3 条线占长 4.6%~6.9%）；
副作用是分母变小可能抬高结果（`SJC4` 因此比两端点法高 1.9%），汇总区会报出。

**顺带记住一个反直觉结论**：三种口径的比降最多差 40%，但**设计水位 Hs 只差 0.01~0.02 m**
（√S 压缩差异 + 水位变化同时改变 A 与 R 形成补偿）。
真正影响结果的是"占位值 0.005 → 真实比降"，那会让 Hs 变 0.05~1.46 m。

### #11 工程文件里 `null` 对不同字段含义不同

- **比降 / 糙率 / 设计流量**：`null` = **未填写** → 读回 `NaN`
- **分区糙率**（main / left / right）：`null` = **留空、回退统一糙率** → 读回 `None`

两者**不能共用一个转换函数**。`project_io` 里 `_nan()` 与 `_opt()` 就是为区分它们而存在。
改错会让「未填参数」与「明示留空」互换，直接影响 `Section.validate()` 的告警
与 `roughness_for_zone()` 的取值——**静默算错，图上看不出来**。

另外 JSON 规范不支持 `NaN` / `Infinity`，必须写成 `null`。
测试里断言了工程文件中不出现这三个字面量（否则其他工具/语言解析会失败）。
Python 的 `json.dump` 默认会写出 `NaN`，那是非法的。

**v2 又加了第三种含义**，别搞混：

- **手动覆盖字段**（`thalweg_manual` 等）：`null` = **未人工干预** → 读回 `None`

而且覆盖字段是**测点索引**，读回时必须是 **int**，不能用 `_opt()`（它 `float()` 一下
把 3 变成 3.0），因为 `is_valid_index()` 用 `isinstance(idx, int)` 判定 → 3.0 会被
判成非法、静默退回自动，表现为"存进去了却没生效"。所以有专门的 `_oint()`。

`_obool()` 同理只认 JSON 的 `true`：写成 `bool(v)` 时，手工改文件写出的字符串
`"false"` 会被判成 `True`，语义从"不分区"翻成"手动分区"，界面上看不出来。

### #12 曲线行数必须**实际数**，不能按 `Δ/dH` 估

手动深泓点会让 H~Q 曲线的起算水位抬高。要告诉用户"少了几行"时，
❌ 不能写 `ceil((dmin_manual − dmin_auto) / dH)`。

水位序列是 `dmin + k·dH` 的**等差数列**，深泓点一动，**整条网格整体平移**，
与上端 `zymin` 的对齐关系随之改变，行数差会落在 `floor(Δ/dH)` 或 `ceil(Δ/dH)` 上。

实测 31 个断面里 **21 个**与 `ceil` 估算差 1 行（例：抬高 0.43 m 实际只少 2 行，
而 `ceil(0.43/0.1)=5`）。当时面板那句话就是这么错的，是被
`tools/check_overrides.py` 抓出来的。

正确做法：`rating.hvec_row_counts(info, cfg)` 直接按 `matlab_colon` 数两遍。
`info.zymin_auto` 就是为这个存的——**不能顺手用生效值的 zymin**，
岸顶是按深泓点分左右取的，深泓点一动手动值的岸顶划分就变了。

**通用教训**：凡是"算出一个数给用户看"的地方，都要拿真实链路去对一遍，
别用看起来等价的近似公式。近似在这里几乎总是差 1。

### #13 手动调节（分区 / 成灾水位）的输入只走图上拾取

菜单栏「分区调节」「成灾水位」两个面板**没有任何数值输入框**，一切靠
在主界面「断面形态」图上点实测测点。这不是省事，而是数据结构决定的：
分区边界与深泓点在 core 层就是**测点索引**，落在两个测点之间的位置
没有任何字段能表达；成灾水位也约定取自某个测点的高程。

配套要注意的五点：

1. **「转折点」与「分区边界」是两个概念，别合回去**（Q15）。
   原 MATLAB 让它们共用同一对索引（分区直接由转折点拼出来），图省事；
   结果手动调分区时转折点与成灾水位被一并拖走，用户指出这是两回事，已拆开：

   | | 字段 | 谁来定 | 决定什么 |
   |---|---|---|---|
   | 转折点 | `left_turn_idx` / `right_turn_idx` | **永远自动**（扫描算法） | **成灾水位** |
   | 分区边界 | `zone_left_idx` / `zone_right_idx` | 默认取转折点，可人工覆盖 | `zones` 与各分区糙率取值 |

   即：**改分区边界不得改动转折点与成灾水位**。`analyze_terrain` 里这两条路
   是分开算的，`_auto_disaster` 只吃转折点。护栏：
   `test_manual_zones_do_not_move_turning_points_or_disaster`、
   `test_default_zone_boundaries_equal_turning_points`，以及
   `tools/check_overrides.py` 在真实数据上逐断面验证（29 个可试验断面全过）。
   注意：手动改**深泓点**仍会带动转折点——扫描是从深泓点向两侧做的，起点变了
   结果自然变，那是算法定义，不是概念混淆。

2. **边界必须分别落在深泓点两侧**，且左 < 右。校验写两处、
   必须用**同一个**判定：`Section.override_errors()`（给界面提示）与
   `terrain._zone_override()`（实际采用）。踩过的坑是前者只看 `thalweg_manual`，
   没手动设深泓点时该值是 `None` → 校验一律通过，而 terrain 却按**自动**深泓点
   把边界否掉，表现为"设置没生效"。所以 `override_errors` 用 `thalweg_index()`
   （生效值），深泓点的取法则与 terrain 共用 `model.argmin_index()`。

3. **单断面模式下分区不参与计算**（`rating.py` 里直接 `zones=[(0,n)]`），
   此时点「图上拾取」会被当场拦下。但**深泓点不受此限制**——它同时决定
   曲线的起算水位，两种模式都用。

4. **深泓点是"全面顶替"**（用户确认）：分区锚点、曲线起算水位、左右岸顶的划分、
   水面交点搜索全用它。所以 `terrain` 里必须用 `_peaks_around(z, di)` 按**最终**
   深泓点重新取岸顶，不能沿用自动值那次的岸顶。

5. 手动值一律以 `None` 表示"未覆盖"。`zone_manual=True` 且两侧都 `None`
   表示「不分区」（1 区）——它与"全自动"结果可能相同但语义不同，必须能表达。
   用「布尔 + 两个可空索引」而不是一个列表，就是为了避免 JSON 里
   `null`（全自动）与 `[null, null]`（不分区）混淆。

**顺带一个容易误解的物理事实**：即使各分区糙率相同、过水面积与湿周总量不变，
**改分区边界也会改变流量**。因为曼宁公式里的水力半径 `R = A/P` 是**逐分区各算各的**
（复式断面的标准做法），各分区 R 不同，`Σ A_i·R_i^(2/3) ≠ A_tot·R_tot^(2/3)`。
实测把主槽两侧各收窄一格，设计水位变 −0.46 ~ +0.04 m。
所以"分区落在哪"不是纯粹的表示方式，它影响结果——转折点（滩槽分界）才是有
物理含义的分界位置。

---

### #14 导航工具栏会"吃掉"点击：进拾取前必须复位

matplotlib 的 `NavigationToolbar2` 处于**平移/缩放**模式时，左键点击被它消费掉，
但 `motion_notify_event` 照常发出。于是拾取功能会表现成：

> 悬浮吸附还在显示，点击却**毫无反应**

用户几乎不可能自查出来——光标只变成小手，而那个按钮就在图上方，极易误触。

`toolbar.mode` 的坑：matplotlib 3.6+ 它是**枚举**，`_Mode.PAN` 的值是
`'pan/zoom'`（**真值**），`_Mode.NONE` 的值是 `''`。所以：
- ✅ `if toolbar.mode:` 判"是否在某种模式" —— `NONE` 时 `''` 为假、`PAN` 时为真
- ❌ `if toolbar.mode == "pan":` —— 枚举与字符串比较，永远不相等
- ✅ 按**名字**判：`(getattr(mode, "name", None) or str(mode)).upper()`

`section_view.begin_pick()` 里已强制复位（`_reset_toolbar()`），`_toolbar_idle()`
只是兜底。护栏：`gui_smoke` 会先进平移模式、再 `begin_pick`、断言 `_toolbar_idle()`。

**顺带一条通用原则**：交互失败**不要静默**。
点偏了（没落在测点上）原先直接 `return`，用户只会反复点、以为功能坏了；
现在发 `pickMissed` 信号，主窗口明确提示"请点在图上的小圆圈上"。

---

## 六、数据与当前状态

`data/` 下 5 个 xlsx → **8 条纵断面线 / 31 个横断面**：

```
sls2-段1(6) sls2-段2(3) sls2-段3(2) yqc6(7)
SJC4(3) ypc6(3) xlc7-段1(3) xlc7-段2(4)
```

数据格式与块分类见 `README.md`「输入数据格式」。
块名匹配用**前缀**（新数据里是「纵断面1」「纵断面2」，不是精确的「纵断面」）。

**参数集 xlsx 不提供**，参数在界面上手工填写（批量填写 + 单断面精调）。
跑通流程时用的是占位参数（糙率 0.03 / 比降 0.005 / Qs 50），
**实际结果必须换成真实参数**。

自动推算出来的分区情况（可用 `tools/check_overrides.py` 重出）：
**多数断面只分成 2 区**（一侧扫不出转折点），少数 3 区。这是数据决定的，
不是程序的默认行为。

已验证：单测全绿 / 界面三视图出图正常 / 冒烟与 exe 自检通过 /
8 条线全部正确归组无遗漏 / 24 个 CSV 编码核验全 OK / 工程文件含手动设定往返一致。

---

## 七、未闭环

1. **与旧 MATLAB 输出的逐列数值 diff**（P2 闸门）。
   用户手上有旧 Output CSV 时，开 `Config.compat_drop_last_point = True`
   （复现 MATLAB 丢掉每个断面末测点的行为）跑一次逐列比对即可闭环。
2. **「桥」块是否参与计算**待用户拍板。目前按 Q11「非编号块排除」全部排除，
   但新数据里桥块带编号了（`桥1`~`桥4`），可能希望纳入。
3. `sls2-7` 的成灾水位比设计水位高 5.4 m，疑似右岸坡长缓导致转折点扫描偏远。
   现在有两条路：调陡坡/缓坡阈值（「计算设置」），或直接「分区调节」手动指定边界。

---

## 八、文档地图

| 文件 | 内容 |
|---|---|
| `README.md` | 给人看的：安装、使用、数据格式、输出说明 |
| `项目方案.md` | 设计与决策记录：缺陷表 D1~D10、决策 Q1~Q14、每次返工复盘、实施进度 |
| `CLAUDE.md` | 本文：给 AI 助手的约束、架构速查、踩坑清单 |
| `.workbuddy/memory/` | 逐日工作日志（append-only，含详细技术细节）。**改数值逻辑前建议先翻一遍** |
| `.workbuddy/memory/MEMORY.md` | 项目的长期约定（按需建立） |
