# CLAUDE.md

面向 AI 编码助手的项目上下文。**改动代码前请先读完本文**，
它记录的每一条都是本项目已经真实踩过的坑，重复踩会直接产生错误结果或崩溃的 exe。

---

## 一、这是什么

把原 MATLAB 河道断面「水位–流量关系曲线」计算脚本（`original_matlab/*.m`）
重写为 Python 桌面程序（PySide6 + matplotlib），并增加三类可视化。

**核心目标是与原 MATLAB 数值一致**，其次才是易用性和功能扩展。
因此存在一条铁律：

> 任何改动都先跑 `tests/test_core.py`，必须**全绿**。
> 涉及数值逻辑的改动，必须能说清它与 `original_matlab/` 里的对应关系。

项目不是从零设计——`DESIGN.md` 是**设计与决策档案**，
含原 MATLAB 实现解构、10 个原实现缺陷（D1~D10）的处置结论、
Q1~Q15 的用户决策记录、以及重写中自引入缺陷（M1~M5）的复盘。
**改设计前先查那里有没有相关决策。**

---

## 二、四条硬约束

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

### 4. 不要换成 PyQt6，也不要给 core 加 pandas

- **PyQt6 是 GPL，PySide6 是 LGPL。** 本程序要分发给外部单位，
  换成 PyQt6 会引入授权风险。**不要因为"PyQt 文档多/示例多"就换。**
  两个库 API 高度相似，改起来很"顺手"，正因如此才要写成硬约束。
- **pandas 不引入。** 数据模型只是每断面几条等长数组，用不上；
  它会额外拖入 pytz / tzdata / python-dateutil / six，且 3.0 有兼容风险。
  依赖越少，打包越稳、体积越小。

> 出处：`DESIGN.md` §6「环境与依赖」。当时是主动选型，不是将就。

---

## 三、常用命令

全部用工作区 venv，不要用全局 Python、不要 `pip install -g`。

```bat
:: 单元测试（不需要任何第三方依赖，个数以实际输出为准）
.venv\Scripts\python.exe tests\test_core.py

:: 依赖自检
.venv\Scripts\python.exe tools\verify_env.py

:: 命令行批处理（不开界面，跑真实数据）
.venv\Scripts\python.exe tools\run_batch.py

:: 界面冒烟测试（离屏渲染三张图到 output\_preview，含大量断言）
.venv\Scripts\python.exe tools\gui_smoke.py

:: 启动界面
.venv\Scripts\python.exe src\app\main.py

:: 源码自检（离屏跑完整流程，退出码 0/1）
.venv\Scripts\python.exe src\app\main.py --selftest

:: 以上两个都可接一个数据目录参数；不接的话 data\ 缺失时会回退 samples\demo_data\
.venv\Scripts\python.exe src\app\main.py --selftest samples\demo_data

:: 打包 → 产物在 dist\断面计算工具\
.venv\Scripts\python.exe -m PyInstaller build_exe.spec --noconfirm

:: 打包后必须跑这两个：exe 自检 + 图标核验
.venv\Scripts\python.exe tools\check_exe.py
.venv\Scripts\python.exe tools\check_icon.py
```

**改完至少跑三层**：单测（纯标准库、不受界面影响）→ 界面冒烟（模拟真实点击与 Excel 粘贴）
→ 打包后自检（在 frozen 环境里把整条链路再跑一遍）。

- 单测**不读任何数据文件**（用代码内造的 fixture）
- 冒烟与自检**需要数据**，但 `data\` 缺失时会自动回退到 `samples\demo_data\`，
  所以干净 clone 也能跑通；回退时日志里会写「用的是**示范数据**」，不会让人误判

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
| `canvas_base.py` | matplotlib 画布基类；悬停提示框统一走 `place_hover_note()`（见坑 #18） |
| `section_view.py` | 断面形态图；**图上拾取模式**（`begin_pick` / `picked` / `cancel_pick`，只吸附实测测点）；手动与自动的记号区分 |
| `section_editor.py` | **编辑横断面**窗口（左表格 + 右可拖动断面图）；`PointTable` 处理 Excel 粘贴；表格默认只读，见 §五 #16 / #17；窗口跟随主界面换断面，见 §五 #19 |
| `rating_view.py` / `profile_view.py` | 水位–流量曲线 / 沿河纵剖面 |

### `tools/` — 辅助脚本（不是一次性脚本，都值得保留）

`run_batch` 批处理 · `gui_smoke` 界面冒烟 · `check_exe` exe 自检 ·
`check_csv_encoding` CSV 编码核验 · `check_icon` 图标核验 · `font_check` 字体核验 ·
`check_spatial` 分组核验 · `check_slope` 比降推算核对 ·
`check_overrides` 手动覆盖核对（含行数警告的端到端核验）· `verify_env` 依赖核验 ·
`check_project_io` 工程文件逐字段核对（设置 24 项 / 断面参数 31×7 值，
并验证用恢复的设置重算结果一致 → `output\工程文件核对报告.txt`）·
`peek_xlsx`/`scan_sections` 数据窥探 · `make_icon` 生成图标 · `wheel_list` 生成离线依赖清单 ·
`fetch_wheels` 按清单取回离线依赖 · **`make_demo_data` 生成可公开的合成示范数据**
（`samples/demo_data/`，`data/` 缺失时自检与冒烟靠它兜底）

> ⚠ 改了**转折点判定 / 分区 / 分组**相关逻辑后，除了跑真实数据，
> 也要跑一遍 demo_data：那份数据的断面形状是按转折点算法**反推**出来的
> （见 `make_demo_data.SHAPE` 的注释），算法一改它可能就不再产生转折点，
> 而这类退化**不会让测试报错**（3 区断言来自手动覆盖，与数据形状无关）。
> 验证：`--selftest samples\demo_data` 应仍然 PASSED。

### 顶层文件与目录

只列上面模块表覆盖不到的（模块职责不重复；README 的「目录结构」已删，以本节为准）：

```
requirements.txt            依赖清单（版本钉死）
wheels_urls.txt             21 个离线 wheel 的下载直链（fetch_wheels.py 读它）
build_exe.spec              PyInstaller 打包配置
README.md / CLAUDE.md / DESIGN.md   三份文档，分工见 §八
output/                     运行输出总目录（计算结果、各类核对报告、预览图）
docs/images/                README 配图（由 gui_smoke 用示范数据生成）
samples/demo_data/          合成示范数据（见 §六）
samples/demo_project.dmprj  由它生成的工程文件示例
tests/test_core.py          单元测试
src/app/assets/             icon.ico（多尺寸）+ icon.png
original_matlab/            原 MATLAB 脚本，算法对照用
```

⚠ **三个目录刻意不入库**：`wheels/`（约 108 MB 离线依赖，用 `tools/fetch_wheels.py` 取回）、
`data/`（真实测量数据）、`.workbuddy/`（本地工作记录，含本机路径与用户名）。
clone 出来的仓库里拷这些路径会找不到——这是预期，不是坏了。

### 程序图标

图标是**画出来的**（不是生成式图片），保证 16×16 到 256×256 都清晰：
深蓝圆角底 + 白色河道横断面 + 浅蓝水面，外圈一圈浅黄（`#FFFF66`）描边。

```bat
.venv\Scripts\python.exe tools\make_icon.py     :: 重新生成（默认变体 c）
.venv\Scripts\python.exe tools\check_icon.py    :: 核验
```

`make_icon.py` 内置多套配色，`--variant` 切换；**当前采用 `c`「金边」，也是默认值**：

| 变体 | 样子 |
|---|---|
| `c` | **金边**：沿蓝底内缘一圈浅黄 ← 采用中 |
| `none` | 不含任何浅黄（改动前的原样，留作对比基线） |
| `a` | 暖阳：左上角一个浅黄太阳，断面略往右下让位 |
| `b` | 金水：水面附近一条浅黄高光带 |

比选用 `--variant all --outdir 某目录`，四种一起渲染到指定目录，**不动成品**。

改形状编辑脚本顶部常量：`TERRAIN`（断面，归一化坐标）、`WATER_Y`（水位线）、
`PAD` / `RADIUS`（圆角）、`RING_W`（金边厚度）——**四种变体共用同一份几何**，只改一处。
`.ico`（16/24/32/48/64/128/256 七档）与预览图会一起更新。

⚠ 三条：
1. 改完**务必验一条**——`none` 变体渲染出来应与改动前的图**逐像素一致**，
   确保重构没顺手伤到原设计。
2. 换图标后 **exe 里的图标不会自动更新，必须重新打包**才生效。
3. `check_icon.py` 除了看 `.ico` 七档是否齐全，还会**比对 exe 里嵌的是不是当前这张图**。
   后一条是必需的：新旧 `.ico` 的档数通常一样（都是 7 档），只看档数会报"正常"
   而实际嵌的是旧图——这是真踩到过的假绿灯。

### 版本、构建信息与签名

- **版本号**：`src/core/version.py` 的 `VERSION`，手工维护，遵循语义化
  （修 bug → 修订号 +1，新增功能 → 次版本 +1，不兼容 → 主版本 +1）。
- **版本历史**：同文件的 `CHANGELOG`：`(版本号, 日期, 功能列表)`，最新在最前。
  ⚠ 只写**用户能感知到**的东西（内部重构、测试补充不进这张表）。
  **发版时表头必须等于 `VERSION`**，由 `tests/test_core.py::TestVersion` 挡住漏写。
- **构建哈希**：打包那一刻的 git 提交短哈希，由 `build_exe.spec` 调
  `tools/write_build_info.py` 烘焙进 `src/core/_build_info.py`。
  ⚠ exe 里既没有 git 也没有 `.git`，不烘焙就**看不出这个 exe 是哪次提交的产物**——
  这正是加版本号要解决的问题。该文件被 `.gitignore` 排除（每次构建都变）；
  源码运行且没有它时，退而实时调 `git` 查一次，都拿不到才显示"未知"。
- **签名**：`version.py` 的 `SIGNATURE`，显示在「关于」最下方，随「复制信息」一起带走。
  与版本号同源（放 `version.py` 而非界面代码），改签名只需改一处；
  `--selftest` 会断言它存在**且位于最后一行**，防止重构时被删掉或挪位。
- `--selftest` 日志里也会记一行版本与构建，便于事后确认"当时跑的是哪一版"。

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

真实影响：secC-1 少 19.0%、secC-2 少 24.3%、secB-6 少 6.3%。

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
当扫描目标本身就是 `_selftest` 目录时，其下所有子目录（`_selftest\secC`）
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
副作用是分母变小可能抬高结果（`secC` 因此比两端点法高 1.9%），汇总区会报出。

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

### #15 横断面在平面上就是一条直线：`s` 与 `x/y` 必须同步

编辑测点时若只改 `s`（起点距）而不动 `x/y`（平面坐标），会留下**不报错的错**：
桩号（`chainage.py` 取深泓点的平面坐标）、水位交点的平面位置（`solver.interpolate_xy`）、
成灾水位坐标导出（`exporter`）全部停留在旧位置 —— **图上是新断面，导出是旧坐标**。

实测 31 个断面（写临时探针统计，非推测）：

| 指标 | 结果 |
|---|---|
| `s[0]` | 恒为 0（31/31） |
| `s` 单调性 | 严格递增（31/31） |
| `x/y` 折线弧长 ÷ 首末弦长 | ≈ 1.0000（最差 1.0011） |
| 各点偏离首末连线 | ≤ 0.34 m（跨度约 60 m） |
| 归一化后 `s` 与 `x/y` 弧长逐点差 | ≤ 0.003 |

→ **横断面在平面上就是直线，`s` 是沿这条直线的累积距离**，
→ 所以可由 `s` 精确反推 `x/y`：`P_i = P_0 + (s_i − s_0)·u`（`u` = 首末连线单位方向）。
   误差量级 = 原数据自身的直线度偏差（≤ 0.34 m）。

`core/edit.py` 已把这条规则固化（`set_s` 里自动 `recompute_xy`），**不要加"关掉同步"的开关**。

⚠ 两个连带项，缺一个就是隐患：

1. **`ln.chainage` 只在载入时算过**（`reader.py` 里调 `compute_chainage`），
   `_recalc()` 不管它。改了几何必须补一次 `compute_chainage`。
2. **手动覆盖值是测点索引**（`thalweg_manual` / `zone_left` / `zone_right` /
   `disaster_idx_manual`）。增删测点后要平移；删掉的正好是那个点则**清空并明确告知**，
   悄悄改成"指向下一个点"更难发现。

**护栏**：`tests/test_core.py::TestSectionEdit` 锁住联动；`gui_smoke.py` 用真实
`transData` 换算屏幕坐标模拟 press/motion/release，断言"横向拖动后 `x` 真的变了"。

---

### #16 `QTableWidget` 默认是"随便敲个键就改写数据"

`QTableWidget.editTriggers` 的**默认值**是
`DoubleClicked | EditKeyPressed | AnyKeyPressed`。那个 `AnyKeyPressed` 的含义是：
**单元格被选中后，敲任何一个字符键立即进入编辑态并把原值替换掉**。

只把 item 的 `ItemIsEditable` 标志去掉 **挡不住这个** —— 那是两个独立开关。
要真正只读，必须显式 `setEditTriggers(QTableWidget.NoEditTriggers)`。

断面编辑窗口最初就栽在这里：表格看着是"只读展示"，实际用户想按键盘配鼠标多选，
就把某个高程改掉了；表格里只有两列数字，改没改根本看不出来。
现在默认 `NoEditTriggers`，要在表格里录入必须显式勾选「允许键入 / 粘贴」，
解锁时用 `DoubleClicked | EditKeyPressed`（**故意不含 `AnyKeyPressed`**）。

同时删掉了打开窗口时的 `setCurrentCell(0, COL_Z)` 预选——
"预选单元格 + AnyKeyPressed" 是同一个事故的另一种触发方式。

**护栏**：`--selftest` 与 `gui_smoke.py` 都断言
`editTriggers() == NoEditTriggers` 且解锁后 `& AnyKeyPressed == 0`。

### #17 Excel 粘贴必须整批校验后再写

Qt 默认的 Ctrl+V 会把**整段剪贴板塞进一个单元格**；Excel 复制出来的是
制表符分隔文本（列间 `\t`、行间 `\n`），必须自己拆开按行列铺开
（`dialogs.PasteTable` 与 `section_editor.PointTable` 都是这个套路）。

比"怎么拆"更重要的是**什么时候写**。`edit.set_s` 对越界值是**安静地夹取**、
对 NaN 是**安静地忽略**，都不报错。所以边解析边写会把断面改一半：

- 粘 60 个数，第 30 个是脏数据 → 前 29 个点已经改了，第 30 个起没改；
- 图上只表现为"形状有点怪"，事后无法判断哪几个点被动过。

正确顺序：**先全部解析 + 校验（非数字 / NaN / inf 一律记为错误），
一处不对就整批放弃并列出前几个出错位置，断面一个点都不动；
全部合法才统一写入，并且只发一次 `edited`**（整批也算撤销栈里的一个动作）。

**护栏**：`gui_smoke.py` 里插一段含"高程"二字的剪贴板，断言粘贴返回 0 且
`sec.z` 逐项未变；另断言越界时行数不变、有明确"忽略 N 行"提示。

### #18 悬停提示框会让 `tight_layout()` 把绘图区挤窄

**症状**：鼠标移到靠右侧的实测测点上，出现"起点距 … 高程 …"的小蓝框时，
**整个绘图区突然变窄**；鼠标移开又弹回来，一闪一闪地缩。
断面编辑窗口与「分区调节 / 成灾水位」拾取窗口都有。

**原因**：`fig.tight_layout()` 会把**所有可见 artist 的包围盒**算进去。
提示框锚在测点上、往右偏十几点，测点靠右时就溢出到轴外；
于是 tight_layout 为了给框腾地方而**压缩绘图区**。
实测（`probe_hover_bbox.py`，断面 secA-6）：悬停第 10 点时轴宽
**551 → 401 px，窄了 27%**。

**修法两层，缺一不可**（都在 `app/canvas_base.py`）：

1. `PlotPanel._exclude_hover_notes()`：给框 `set_in_layout(False)`，
   把它排除出 tight_layout 的包围盒计算——**这才是画布变窄的直接原因**。
   必须**早于** `tight_layout()`。
2. `PlotPanel._place_hover_notes()`：在 tight_layout **之后**，按框的实际
   屏幕宽度决定摆测点右侧还是翻到左侧。必须**晚于** `tight_layout()`。

三个实际踩到的坑：

- **不能在 `place_hover_note()` 里就定位**。它是在 `_refresh_plot()` 中途被调的，
  那时轴还是**上一次布局**的几何：draw 前 `get_window_extent()` 给 573 px、
  draw 后 613 px，差 40 px。拿旧矩形判断"右边放不下"，**每个点都判成溢出**、
  全部翻到左边，比不翻还难看。
- **`xytext` 是点、`get_window_extent()` 是像素**，差 `dpi/72` 倍（dpi=100 时 1.39）。
  把 `bb.width` 直接当点用会把偏移算成几百点、把框甩到轴外，
  而且看起来"确实翻到左边了"，很难发现算错了。
- **认框不能用 `t.xy`**。拾取图层画高亮测点也有个 `Annotation`，
  `xy` 同样等于该点坐标——`t.xy == (s, z)` 会先撞上那个 8.5 px 的圆点，
  量到的是圆点的 39 px 包围盒，看起来"完全在轴内"其实没量到框。
  按 `place_hover_note()` 打的 `gid="hover-note:rad:dy"` 认才准。
  （也不能用 `t.xycoords` 判类型：那是 `Annotation` 才有的属性，
  `ax.text()` 建的 `Text` 没有，直接取会 `AttributeError`。）
- 量之前必须 `canvas.draw()`（同步）：`draw_idle()` 是**异步**的，
  不先真画一次，`transData` 与 `get_window_extent` 都还是旧值。

**护栏**：`gui_smoke.py` 在两个窗口里都遍历全部测点，断言悬停时**轴宽恒定**
（截距 ≤ 0.5 px），并断言最右测点的框翻到左侧且完全落在轴内。
`probe_hover_bbox.py` 是可随时重跑的定量探针。

### #19 编辑窗口跟随主界面换断面：同步点不能只挂在一个信号上

**症状**：主界面换断面（换行、换线）时，编辑窗口纹丝不动，还停在上一个断面上。
用户以为在改 A，实际改的是 B。

**原因不是"忘了接信号"，而是同步点挂得不够**。断面选择有**两条**入口：

| 路径 | 触发 | 注意 |
|---|---|---|
| 同一条线内换行 | `lst_secs.currentRowChanged` → `_on_section_changed` | 正常发信号 |
| **换纵断面线** | `lst_lines.currentRowChanged` → `_on_line_changed` | `lst_secs` 是 **`blockSignals(True)` 重建**的，`setCurrentRow(0)` **不触发** `_on_section_changed` |

只挂在 `_on_section_changed` 上，换线路径就整条漏掉。
（`_refresh_manual_panels()` 早就因为同样的原因被无条件调用过，注释里写着——
编辑窗口当时没跟上这条经验。）

→ 抽一个 `_sync_editor_selection(sec)`，**两条路径都调**。
`follow()` 里有 `if self.sec is sec: return`，所以重复调用是幂等的。

**几个必须一起处理的行为**：

- **有未保存改动时先问一句**，用户选「否」则留在原断面。
  选「否」之后要把 `follow_selection` 置 **False 并保持**，
  否则主界面任何一次同步都会再问一遍（反复追问）。
  该标志在 `_open_section_editor()` 里复位——用户再点一次菜单就该恢复正常跟随。
- **换断面必须清空撤销/重做并重取 `_base`**（直接复用 `set_context()`）。
  撤销栈跨断面会让人一路撤销到**别的断面**的数据上。
- **"有没有改动"的判据是与 `_base` 快照比对，不是"撤销栈非空"**。
  用户改完又点「还原」，撤销栈里仍有历史，但断面已回到原样，这时不该再拦。

**⚠ 测试时的一个大坑**：`section_editor` 是
`from PySide6.QtWidgets import QMessageBox`——把类**直接绑进自己的命名空间**。
只替换 `main_window.QMessageBox` 挡不住 `follow()` 里的确认框，
离屏测试会**永久阻塞**（本次探针就这么卡死过一次，只能靠 SIGTERM 收）。
两个模块都要换。

**护栏**：`gui_smoke.py` 覆盖同线内换行 / 换线 / 选「否」留下且不反复追问 /
选「是」跟随且历史清空 / 还原后不再拦截，共 6 条断言。
`tools/probe_editor_follow.py` 是同一批场景的独立探针。

---

## 六、数据与当前状态

⚠ **`data/` 不入库**（真实测量数据，不随仓库分发；见 `.gitignore`）。
下文描述的是**开发机上那份**数据的情况。换上别的数据时，
这里的分组结果与分区结论都会变，`--selftest` 也可能因数据不符合预期而失败。

**仓库里另有一份可公开的合成数据 `samples/demo_data/`**（3 条纵断面线 / 8 个横断面，
由 `tools/make_demo_data.py` 生成）。`--selftest` 与 `tools/gui_smoke.py` 在
`data/` 缺失时自动回退到它——**所以本节的结论只适用于 `data/` 那份真实数据**，
拿 demo_data 跑出来的条数、分区数都不一样，别照着本节去核对演示结果。

改数据分组 / 分区相关逻辑时，**两份都要跑**：真实数据验证不炸，
demo_data 保证干净环境下也能验证。

⚠⚠ **`.dmprj` 是数据快照，里面装着完整坐标数组。**
`data/*.xlsx` 移出仓库**不等于**数据没进仓库——`samples/demo_project.dmprj`
一度就是那份真实数据的另一种写法（31 个断面的全部 x/y/s/z + 5 个真实文件名
+ 本机绝对路径）。凡是"保存过真实数据的文件"，公开前都要单独查一遍：
**`.dmprj` / 导出的 CSV / 工程备份 / 截图**。
现在 `samples/demo_project.dmprj` 由 `make_demo_data.py` 从示范数据一并生成，
`source.dir` 写成相对路径，不要手工覆盖它。

`data/` 下 5 个 xlsx → **8 条纵断面线 / 31 个横断面**：

```
secA-段1(6) secA-段2(3) secA-段3(2) secB(7)
secC(3) secD(3) secE-段1(3) secE-段2(4)
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
3. `secA-7` 的成灾水位比设计水位高 5.4 m，疑似右岸坡长缓导致转折点扫描偏远。
   现在有两条路：调陡坡/缓坡阈值（「计算设置」），或直接「分区调节」手动指定边界。

---

## 八、文档地图

| 文件 | 内容 |
|---|---|
| `README.md` | 给人看的：安装、使用、数据格式、输出说明 |
| `DESIGN.md` | **设计与决策档案**：原 MATLAB 解构、缺陷表 D1~D10 与 M1~M5、决策 Q1~Q15、环境选型。**不写"怎么用"**（那是 README）、**不写"改代码要注意什么"**（那是本文）。2026-09-23 裁掉了已过时的可视化设计 / 输出格式 / 实施计划三节 |
| `CLAUDE.md` | 本文：给 AI 助手的约束、架构速查、踩坑清单 |
| `.workbuddy/memory/` | 逐日工作日志（append-only，含详细技术细节）。**改数值逻辑前建议先翻一遍**。⚠ **不入库**（含本机路径与面向内部的过程笔记），只在开发机上 |
| `.workbuddy/memory/MEMORY.md` | 项目的长期约定（按需建立） |

**三份文档的分工别混**：想知道"**为什么**这么定"看 `DESIGN.md`；
"**怎么用**"看 `README.md`；"**改之前必须知道什么**"看本文。

⚠ **有三个目录刻意不入库**：`wheels/`（108 MB 离线依赖，用
`tools/fetch_wheels.py` 取回）、`data/`（真实测量数据）、`.workbuddy/`（本地记录）。
所以在 clone 出来的仓库里拷这些路径会找不到。
但**自检与冒烟测试仍然能跑**——`data/` 缺失时会自动回退到
`samples/demo_data/`（`tools/make_demo_data.py` 生成的合成数据，可公开）。
