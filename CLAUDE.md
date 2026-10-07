# CLAUDE.md

面向 AI 编码助手的项目上下文。**改动代码前请先读完本文**，
它记录的每一条都是本项目已经真实踩过的坑，重复踩会直接产生错误结果或崩溃的 exe。

---

## 一、这是什么

把原 MATLAB 河道断面「水位–流量关系曲线」计算脚本
重写为 Python 桌面程序（PySide6 + matplotlib），并增加三类可视化。
（原 MATLAB 脚本属历史参考资料，不随仓库分发。）

**核心目标是与原 MATLAB 数值一致**，其次才是易用性和功能扩展。
因此存在一条铁律：

> 任何改动都先跑 `tests/test_core.py`，必须**全绿**。
> 涉及数值逻辑的改动，必须能说清它与原 MATLAB 实现的对应关系。

项目不是从零设计——`DESIGN.md` 是**设计与决策档案**，
含原 MATLAB 实现解构、10 个原实现缺陷（D1~D10）的处置结论、
Q1~Q15 的用户决策记录、以及重写中自引入缺陷（M1~M5）的复盘。
**改设计前先查那里有没有相关决策。**

---

## 二、四条硬约束

### 1. `src/core/` 保持零第三方依赖

只有 `reader.py`（读 xlsx）和 `exporter.py`（写 xlsx）允许 import openpyxl。
其余模块**不要引入 numpy / pandas**。

**主理由是可测试性**：数值核心这一层不依赖任何东西——core 里 18 个模块有 16 个是纯标准库，
`tests/test_core.py` 的 179 个用例里有 176 个不装依赖就能用系统 Python 直接跑，
"改数值逻辑先跑单测"的门槛才足够低。
次要理由：计算量只有 10⁵ 量级，纯 Python 毫秒级，numpy 的性能优势用不上；
数值核心也不该依赖一个会自己改变归约实现的库。

⚠ **别把它读成"为了跟 MATLAB 对齐"**：
- 与 MATLAB 的 1e-9 逐列一致性（G1 闸门）**至今未实测**（见 `DESIGN.md` §七），
  且日常模式下代码**本来就故意与 MATLAB 不同**（D9 默认修复了丢末测点、D3 顶宽两套口径）
- "累加顺序必须与 MATLAB 一致"只在**你真的跑 G1 闸门时**成立。届时的要求是
  别把逐项累加改成分段/向量化归约——**这只约束回归模式，不是日常约束**

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

**改完至少跑三层**：单测（不受界面影响）→ 界面冒烟（模拟真实点击与 Excel 粘贴）
→ 打包后自检（在 frozen 环境里把整条链路再跑一遍）。

- 单测共 **212 个用例**，分两类（别笼统说"纯标准库可跑"）：
  - **204 个纯标准库**，不需要装任何依赖，系统 Python 直接就能跑
  - **8 个需要 openpyxl**，又分两组：
    - **4 个读 `data\` / `samples\` 的回归测试**（`test_real_data_roundtrip`、
      `test_thalweg_index_matches_terrain`、`test_real_data_roundtrip_with_overrides`、
      `test_loaded_folder_can_be_filled_by_defaults`）——目录不存在时 `skipTest`
      自动跳过；但**目录在、openpyxl 缺时会直接报错**
    - **4 个 `TestDedupeSectionNames`**（重名断面自动改名）——**自己**用 openpyxl
      写临时 xlsx 造夹具，与 `data\` 无关，因此**没有跳过保护**，
      缺 openpyxl 时一律报错。用托管 Python 跑单测就会看到这 4 个红
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
| `reader.py` | xlsx 解析、块切分、块分类、**X/Y 列序判定**（`detect_xy_order` / `resolve_xy_order`，见坑 #21）、空间分组（唯一依赖 openpyxl 的模块之一） |
| `geom.py` | `section_geom`(A/P/B)、`surface_span`、**`wetted_polygons`**（见坑 #2） |
| `terrain.py` | 深泓 / 峰点 / 转折点两阶段扫描 / 成灾水位 / **`_build_zones`**（见坑 #1）；**先算自动值再套人工覆盖**（`_zone_override`、`_peaks_around`）；⚠ **转折点（永远自动）与分区边界（可覆盖）是两条独立的路**，见坑 #13 |
| `rating.py` | `matlab_colon` 水位向量 + 复式分区曼宁求和 + 单调性检查 + **`hvec_row_counts`**（见坑 #12） |
| `interp.py` | `interp1` 线性+外推、水面交点搜索、坐标插值 |
| `spatial.py` | 线段求交、折线最短距离、按相交关系分组（**纯标准库，不要引入 shapely**） |
| `chainage.py` | 里程计算、起始端点重定基、按交点反算桩号 |
| `slope.py` | 按纵断面推算平均比降。默认口径**约翰斯通-克罗斯法**，另有两端点法 / 最小二乘可切换。⚠ 两处易错：① 用 `profile.dist` **原始起点距**坐标系，不是 `line.chainage` 重定基桩号；② JC 法必须用**开方版**公式，线性版恒等于两端点法（见「坑 #10」） |
| `hydro1d.py` | **一维水动力推算**。严格遵循“零依赖”约束，使用纯 Python 标准库编写了标准步长法求解器，支持缓流、急流的水面线推算与临界水深求解。注：局部损失系数取的是待求断面（缓流 sec_up / 急流 sec_down），收缩/扩张由速度比较判定。 |
| `params.py` | 参数手工填写 / 批量修改 / 导入合并（`only_missing` 模式） |
| `solver.py` | 单断面求解编排 |
| `project_io.py` | 工程文件（`.dmprj`，单个 JSON 文本）读写。全量快照，**不存计算结果**。⚠ `null` 对不同字段含义不同，见「坑 #11」 |
| `exporter.py` | 三个 CSV + xlsx（另一个依赖 openpyxl 的模块），以及一维推算结果导出 |

### `src/app/` — PySide6 界面层

| 模块 | 职责 |
|---|---|
| `main.py` | 入口；`--selftest` 自检；字体与图标设置 |
| `main_window.py` | 主窗口，菜单栏、列表、重算调度、导出；手动覆盖的写入与校验（`_try_apply_override`） |
| `dialogs.py` | 顶部菜单栏弹出的五个非模态面板：计算设置 / 批量填写 / 比降推算 / **一维水面线推算** / **分区调节 + 成灾水位**（后两个见 §五 #13） |
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
original_matlab/            （本机）原 MATLAB 脚本，算法对照用；**不入库**，见 `.gitignore`
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

### #20 一维水动力推算的局部系数隐式约定（`hydro1d.py`）

写在这里而不是 §四，是因为它属于"改了会算错但看不出来"的那一类。

- 做一维推算（标准步长法）时，局部损失系数（收缩/扩张系数）取的是**待求断面**的
  系数配置：缓流（下游推上游）取 `sec_up` 的、急流（上游推下游）取 `sec_down` 的。
- **收缩还是扩张由流速判定**：待求断面流速大于已知断面 -> 收缩系数（默认 0.1），
  否则扩张系数（默认 0.3）。
- ⚠ 之前这条以 `20.` 的编号形式**误挂在 §八 文档地图末尾**，与「坑清单」的
  `### #N` 体例不一致，找也找不到。已归位为 #20。

### #21 坐标约定：**内部恒为测量坐标系**，X/Y 别按数学习惯想

**约定**：`Section.x = 北坐标（纵向）`、`Section.y = 东坐标（横向）`。
⚠ **与数学直觉相反**（x 不是横向）。写任何涉及坐标的逻辑前先看这一条。

**导入**：xlsx 前两列哪个是北由 `reader.detect_xy_order` 自动判定
（列头名 + 数值量级两层），北列进 `x` —— **与输入文件的前两列顺序无关**，
用户也不需要设置（用户明确要求删掉了设置里的那一项）。
判不出来时（局部坐标、列头被删）弹窗问一次，写进 `Config.first_col_is_north`
（**该字段没有界面入口**，别以为它没被用上）。

**两个必须记住的技术点**：

1. **改动影响面比看起来小**：互换前两列 = 平面关于 $y=x$ 镜像，
   而镜像是**等距变换**。全量扫过 `src/` —— **没有任何代码对 X/Y 轴做非对称操作**
   （分组/桩号/插值/面积全用 `(x,y)` 成对或只用 `s`、`z`；一维推算的上下游判的是
   河底高程）。所以这类改动**不会**改变任何水力结果，只影响"哪一列写第 2 列"。
   改完请用基线比对确认：见 DESIGN.md §4.8 的实测方法（`git archive` 取旧代码跑基线）。
2. **裸 `X` / `Y` 不构成证据**。中国测量惯例 X=北、数学/CAD 习惯 X 常指东，
   而"列头写 `X坐标` 装北坐标"与"列头写 `X坐标` 装东坐标"两种文件
   **文字层面完全一样**（真实数据就是前者）。强信号只有「北/纵/N/northing」
   与「东/横/E/easting」，其余交给数值量级。

**导出**：一律经 `exporter.out_xy()` 这一个出口，按 `cfg.export_coord_system` 决定
原样还是交换（`survey` 原样 / `math` 交换）。**新写任何导出坐标的代码都必须走它**——
绕过去不会报错，只会让坐标悄悄镜像。
`model.Section.duanmian_xy()` 已删除（输出格式却住在 model 里、还硬编码列序）。

**改动位置要记牢**：判定放在 **`load_file()`**，不放 `blocks_to_sections()`
（后者被 10 处单测直接调用，放进去会把既有测试全拖进来）；
用 `dataclasses.replace(cfg, first_col_is_north=…)` 生效，下游签名不变。
导出坐标系挂在 `SettingsDialog.exportChanged`（只同步配置、**不重算**），
不要混进 `changed`。

**护栏**：`tests/test_core.py::TestXYOrder`（列头/量级/冲突/判不出/内部恒为北/
两种输入列序给出相同几何）与 `TestExportCoordSystem`（两档互换、列名不变）。

---

### #22 有**两条**装载路径，参数填补必须各自接上

`reader.load_folder()` 是**纯数据层**：按设计把糙率/比降/Qs 留成 `NaN`，
"填默认值"是**调用方**的责任。主窗口里负责这件事的是
`MainWindow._fill_missing_params(secs)`（内部只调一次 `P.apply_batch(..., only_missing=True)`）。

**两条路径都得接**：

1. `MainWindow._load()` —— 打开工程 / 启动时载入；
2. `MainWindow._pick_and_load()` —— 菜单「导入 excel 数据…」，走
   `ImportDataDialog` 取 `get_project()`。

**踩过的坑**：PR #3 把导入抽成独立对话框后，新路径直接接管了 `self.project`
却没有调填补，而 `utils.P_toSection()` 是纯构造函数、也不会填。
结果是**导入完参数一片空白、三张图全空**——用户原话是"现在导入数据不再填写默认参数，
导致无法计算"。表现是静默的：不报错、不缺数据，只是不计算。

**所以**：任何新增的装载入口（拖拽、命令行、工程合并…）都必须调
`_fill_missing_params()`，并顺带 `_sync_cfg()` / 刷新 `_update_title()` /
`dlg_batch.set_result(...)`，否则界面与数据会不同步。

⚠ `P.apply_batch(secs, only_missing=True, **fields)` **必须显式给出字段值**：
不传的字段会被填成 `0`，而不是"跳过"。

**护栏**：`TestParams.test_loaded_folder_can_be_filled_by_defaults`（锁死
"load_folder 产物必须能被默认值填齐 + only_missing 不覆盖已有值"）。
冒烟里的对应断言见 `tools/gui_smoke.py::_check_import_dialog`
（走 `dlg._read_dir()` 而不是绕过它直接 merge，否则"漏处理"测不出来）。

---

### #23 第二条路径同样会漏「列序询问」：判不出来必须留痕

同一个根因的另一半。#22 说的是**参数填补**漏了，这条说的是**列序询问**也漏了，
而且症状更隐蔽：

- `_load()` 里询问逻辑被 `not getattr(self, "_xy_asking", False)` 把门。
  而 `_pick_and_load()` 会**再调一次 `_load()`**（嵌套），那时 `_xy_asking`
  还是 `True` ⇒ 内层既不弹窗、也不记疑，数据按**默认列序**静默载入。
- `ImportDataDialog._on_load_dir()` 原来调 `load_folder(...)` **不传 `xy_report`**，
  判定结果被整个丢弃 ⇒ 同样不弹、不说。

用户原话："自定义的坐标列序不弹出来"。后果是**导出的平面坐标两列可能颠倒**
（水力结果不受影响——镜像是等距变换，见 #21），而界面上毫无线索。

**现在的写法**（改之前先读懂）：

1. `_load()` 里无论"问了没确认"还是"嵌套调用"，都记进 `self._xy_pending`；
2. 询问逻辑抽成 `MainWindow._ask_xy_order(ask)`，返回 `True/False`，
   用户关窗返回 `None`；
3. `ImportDataDialog._read_dir()` **自己处理**：判不出来就当场问（调
   `self._ask_xy_order`，可被冒烟替换），用户认了就立刻用新列序重读一遍；
4. `_xy_pending` 非空 → 状态栏加一句可操作的话，
   并且「计算设置 → 输入坐标列序」能看到当前判定 + 有个「坐标列序…」按钮
   **手动复核**（这是"弹窗被关掉后还能救回来"的唯一入口，别删）。

⚠ 每轮 `_load()` 开头都要 `self._xy_pending = None`，否则上一轮的存疑会累积。

---

### #24 冒烟 / 自检脚本的参数与离屏陷阱

**坑 A：数据目录只认位置参数。**
`tools/gui_smoke.py` 原本只读 `sys.argv[1]`，写成 `--data samples/demo_data`
时会把 `--data` 当目录名——**不报错**，而是卡在扫描一个不存在的目录上，
日志 **0 字节**、看着像"卡死/跑不完"。`main.py --selftest` 同样是 `argv[0]`。
现在两处都支持 `--data X` / `--data=X` / 位置参数（见 `main._pick_data_dir`、
`gui_smoke.resolve_data_dir`），且**指定了不存在的目录会明确报出来**，
不再静默回退到别的数据源——否则"我以为跑的是 A 数据，其实是 B"。

**坑 B：脚本结果不打印到 stdout。** `gui_smoke.py` 把报告写成 `_gui_smoke.txt`，
只看 stdout 会以为它没跑。看输出要 `cat _gui_smoke.txt`。

**坑 C：offscreen 下任何模态弹窗都会永久阻塞。**
每一处会弹 `QMessageBox` / `QDialog` 的地方都必须先装替身，
用完在 `finally` 里还原。已知必须替换的：`QMessageBox.question`（`_new_project()`
→ `_maybe_save()` 会弹"要保存吗"）、`ImportDataDialog._prompt_resolution`、
`ImportDataDialog._ask_xy_order`。

**症状识别**：进程被 SIGTERM 杀掉、日志 0 字节 ⇒ 先怀疑模态弹窗或
输出缓冲（用 `python -u` + `PYTHONUNBUFFERED=1` 排除后者）。

**坑 D：冒烟跑完记得把状态还原。** 新加的检查若替换了 `win.project`
（对话框返回的是 deepcopy），末尾必须 `win._load()` 复位，
否则后续"纵剖面跟随列表选择"之类的断言会连带失败。

---

### #25 `results` 按断面名做键：重名断面会静默互相覆盖

**现象**：两个断面同名时，后算出的那个**悄悄顶掉**前一个的结果——
导出 CSV 里两条断面的水位/流量同源、一维推算取到错的设计水位、
图上两条线照画不误（只是数值一样）。全程不报错，肉眼看不出来。

**根因**：结果容器全是名字做键——`MainWindow.results` / `.infos`、
`exporter.export_all`、`hydro1d.compute_hydro1d_profile` 里
`results.get(sec.name)`。名字一撞，就是后写覆盖先写。
纵断面线名早就有 `reader._dedupe_names` 去重，**横断面名这一半一直缺**。

**修法**：`reader._dedupe_section_names(loaded, warnings)`，
在 `_dedupe_names` 附近调用——但必须放在 `_build_lines` **之前**，
因为分组、桩号、`attach_params` 全都按名字办事，改晚了会一路错到底。

**两条必须守住的约定**：
1. **方案 A：只在真重名时改名**。不重名的数据一个字符都不许动，
   否则会把下游按断面名对账的脚本、工程文件里的参数记录全部弄断链。
2. **同时改 `sec.params.name`**。两者在 `params.ensure_params` 里有
   "保持一致"的约定，工程文件里各存一份；只改一边会让参数面板/导出
   按旧名去找，改名反而**制造出**"参数丢失"。

后缀用 `@文件名`（如 `secA-1@b`）而不是 `-段2`：断面重名基本都发生在
"不同文件各有一块同名断面"，带上来源用户才看得出分别是哪来的。
同文件内原名重复时再补 `-2` / `-3`（有 `taken` 集合防撞名）。

### #26 糙率是**三处联动**的，不能一处按分区、另一处按统一

**现象**：同一个断面出现两套糙率模型。H~Q 曲线（`rating.py`）用的是
**分区曼宁**，而一维推算的摩阻比降改用了**统一糙率**的 K。
`Sf = (Q/K)²`，K 差 50% ⇒ Sf 差 **4 倍**（是平方关系，别按线性想），
推算出的水面线整体失真，界面上完全看不出来。

**三处必须同口径**：`rating.py` 的 H~Q 曲线、动能修正系数 α、
`hydro1d` 的摩阻比降 Sf —— 后两者都以流量模数 K 为中间量。

**修法**：`hydro1d.zone_conductance(sec, Z, cfg, A_sub, P_sub)` 是
**唯一口径**，返回 `(K_tot, [K_i, ...])`，α 与 Sf 都复用它。
遍历**直接走 `info.zones`（含重叠区间）**，与 `rating.py` 完全一致——
别自作聪明去"去重叠"，那会漏算面积（见硬约束 #2 / 坑 #1）。

**⚠ K 必须无条件按分区算**，不能挂在 `cfg.kinetic_alpha_auto` 下面。
那个开关只管"要不要算 α"，跟"用哪套糙率"是**两件事**；
挂上去会让用户关掉自动 α 时 K 悄悄退回统一糙率，两条链路又分叉了。
（这条是 1.4.2 修的：此前 α 和 K 一起被开关管着，关掉时"内部自洽"
但与 H~Q 曲线不一致。）

**兼容性**：只填统一糙率的工程，K 与旧公式 `A·R^(2/3)/n` **逐位相同**，
结果不变。填了分区糙率的工程**数值会变**——这是修正不是破坏，
但同一份数据的新旧导出对不上，对账时要知道。

### #27 二分求根的区间是**写死**的：贴边假解必须报出来

**现象**：搜索区间是硬编码的 `[min(z)+0.01, max(z)+10]`。下游水位很高、
回水上溯超过 `max(z)+10` 时，**真解落在区间之外**，二分一路贴到边界，
把边界附近的假解当答案返回——不报错、不警告，用户拿到一个平白低
十几米的水位，还以为是算出来的。

**⚠ 判据不能写成"看解距离边界多远"**（这是第一次改错的地方）：
二分只要没提前收敛，区间宽度就会被压到 `<= tol` 才退出，此时解距离
**原**边界恰好还有约 `tol/2`（`mid` 永远取区间中点，所以浮点余量是
`tol/2` 量级）。任何 `tol` 量级的**绝对**容差都测不出贴边——
`1e-6` 更是完全测不出来。正确判据是：

> **区间耗尽（宽度 `<= tol`）且残差未收敛** ⇒ 真解在区间外，结果不可信。

这条同时覆盖"残差恒定不变/符号永不翻转"（区间照样被耗尽）和
"残差发散到 1e14"两种情形。反过来，真解**靠近**边界但确实收敛时
（例如离上界 0.3 m），`converged` 为真，**不能**误报——否则用户会对
正常结果失去信任。

**诊断通道用"出参"而不是回调**，与 `reader.load_folder(..., xy_report=list)`
同一套路：core 层不认识界面，所以由调用方传入一个 list。
链路是 `compute_hydro1d_profile(..., warnings=list)` →
`solver.solve_profile_line(..., warnings=...)` → `MainWindow._solve_all`
→ 状态栏（`_recalc` / `_load`）。**加诊断时不许动数值**：
`_bisect_detail` 与原循环在 5 类场景下必须逐位相同（有回归测试守着）。

---

### #28 导入窗口左栏是"立即生效"，取消不还原——别再删掉「撤销删除」

**背景**：`ImportDataDialog` 双栏重构时，为了"删除立刻反映到主界面"，
把原来的 `copy.deepcopy` 沙箱去掉了，改成**直接引用主界面那个工程对象**
（`self.current_project = current_project`，`get_project()` 返回同一个对象）。
于是左栏删除绕过了 `accept()` 这道闸门，**点一下按钮就落到真数据上**。

**由此产生的一条硬约定**：

> 左栏删除**立即生效**，而且**关闭/取消本窗口不会还原**。

这不是 bug，是有意选的交互（让用户边删边看主界面变化）。但既然放弃了
"取消即撤销"，就**必须**自己留一条回退的路：`__init__` 里的
`self._baseline_lines = copy.deepcopy(current_project.profile_lines)`
（打开窗口那一刻的快照）配上「撤销删除」按钮。

**⚠ 后人最容易犯的错**：看到 `_baseline_lines` 和「撤销删除」觉得是冗余，
顺手删掉——那就把删错数据的最后一条退路也断了。`tools/gui_smoke.py`
里有三条断言守着（删除生效 / 撤销恢复数量 / 撤销连手动设定一起还原）。

**几个实现要点**：
- 快照取在**构造时**，所以"撤销"恢复的是"打开窗口那一刻"，不是"上一次操作前"。
  写测试时要注意：想验证"手动设定被还原"，必须在**构造对话框之前**打好记号，
  否则测的是别的东西（第一次就写反了，冒烟直接红）。
- 撤销用**整份快照回滚**，而不是"记住删了哪些再插回去"——后者要还原
  列表位置与对象身份，中间再有别的改动就会错位。
- `_on_delete_left` 是**唯一**在 `accept()` 闸门外改活工程的路径。
  `_merge_project` 只在 `_on_apply` 里调用、紧接着 `accept()`，是正常的；
  `_on_delete_right` 只动 `new_project`（待导入的副本），安全。
- 左栏改动靠 `_sync_to_parent()` 通知主界面（`_set_dirty` / `_solve_all` /
  `_refresh_line_list` / `_refresh_current_views` / `param_panel`）。
  里面用 `getattr` 逐个探——对话框在测试里可能挂到非 MainWindow 的 parent 上。

---

### #29 `setEnabled` 与 `setToolTip` 必须成对改

`_ManualBase._sync_buttons()`（以及 `HydroLossDialog` 的填表逻辑）里，
每个控件都是「改可用状态」+「改提示文本」**成对**出现的：

```python
bc.setEnabled(ok and is_man)
if not ok:
    bc.setToolTip("请先选择一个有结果的断面")
elif not is_man:
    bc.setToolTip("当前为自动推算，无需清除")
else:
    bc.setToolTip("清除手动设定，恢复为程序自动推算")
```

**为什么必须成对**：按钮灰着却不说明原因，用户只能猜"是不是程序坏了"。
而这类漏改**不会报错**——界面照样跑，只是那个按钮永远在说一件过时的事
（比如"请先选择一个断面"，其实断面早选好了）。

**⚠ 重构时最容易犯的错**：只保留 `setEnabled`、顺手删掉 `setToolTip`。
所以 `tools/gui_smoke.py::_check_manual_tooltips` 钉住了三种状态
（未选断面 / 全自动 / 有手动设定），并验证提示是**按字段**刷新的
（同一断面里手动字段说"清除…"，自动字段说"无需清除"）。

这条护栏做过**变异测试**：把禁用态的 `setToolTip` 清空，冒烟立刻红在
`assert cl_tip.strip()`。改动 `_sync_buttons` 后请跑一遍冒烟确认它还是绿的。

---

### #30 参数 CSV 的「糙率」列 = **主槽糙率**，且导出要「主槽优先」

`_export_params_csv` 的「糙率」列取的是 `roughness_main`，**不是** `roughness`：

```python
n_main = p.roughness_main
if n_main is None or n_main != n_main:   # 主槽没填才回退统一糙率
    n_main = p.roughness
```

**⚠ 别抄 `dialogs.BatchDialog._refresh_roughness_table` 的写法**。那里是
`roughness` 优先（`main = p.roughness; if not(main==main): main = p.roughness_main`），
因为**界面显示**两者本该同值、取哪个都一样。但**导出到 CSV 是要能原样回灌的**，
一旦按「统一优先」写，"主槽 0.030 + 统一 0.035" 的分区断面回灌后主槽就变成 0.035
（实测过，且状态栏只报"更新了 N 个断面"，一个字不提字段被改）。

导入侧对应地**按分区与否分流**（与批量对话框 `collect()` 同口径）：

| CSV 里左右滩 | 认定模式 | 「糙率」列写到 |
|---|---|---|
| 有值 | 分区糙率 | `roughness_main`（`roughness` 保持不动） |
| 都留空 | 整断面糙率 | `roughness`，并把三个分区字段清成 `None` |

注意 `None` 与「数值 == 统一糙率」在 `roughness_for_zone` 下**完全等价**
（`None` 会回退到 `roughness`），所以整断面模式把它规范成 `None` 是**无害的**，
别把冒烟断言写成逐字段严格相等——那样会误报。

### #31 导入外部表格：解析失败必须**跳过并保留原值**，绝不许写 NaN

`_import_params_csv` 里非法数字（`120 m3/s`、`1,200`、`0.035。`、全角负号…）
走的是「跳过该格 + 保留原值 + 汇总提示」，**不是**写 `float("nan")`：

```python
if not ok:
    bad_cells.append((lineno, name, field_cn, text.strip()))
    return cur          # 保留原值
```

**为什么这条特别要紧**：`float()` 会把 `nan` / `inf` 字面量也吃进去，所以
`parse_cell` 额外把它们判为非法。而**设计流量一旦变成 NaN**，
`hydro1d.py:390` 会**悄悄用 50.0 顶替**继续算——出一条看起来正常的错水位，
全程零告警。这正是项目里反复出现的那类"静默失败"。

提示文案要落到「第 N 行「断面名」的某字段：'原文本' 不是有效数字」这种粒度，
只报一个总数等于没说。`tools/gui_smoke.py::_check_params_csv` 钉住了
「3 处非法格 → 字段全保留、无 NaN、warning 被调用」，并做过变异测试。

---

### #32 **别把"改代码脚本"提交进 `tools/`**

2026-10-07 发现机器人往 `tools/` 提交了 5 个这样的文件：

```python
# tools/add_export_checkbox.py（已删）
content = open(filepath, encoding='utf-8').read()
content = content.replace(search, replace)     # 盲替换
open(filepath, 'w', encoding='utf-8').write(content)
```

它们是机器人用来改源码的一次性补丁，**不是工具**。两个实证危害：

1. **不幂等，再跑一次会损坏源码。** `add_export_checkbox.py` 的 `search` 串
   在改动落地之后**仍然匹配**（被替换的那两行还在原位），所以再执行一次会往
   字典里**重复插入**同一个键。Python 的重复字典键**不报错**（后者覆盖前者），
   属于静默出错。
2. **有的从一开始就没生效。** `add_export_all_table.py` 的 `search` 串写的是
   `'hydro1d': 一维推算水面线.csv`，而真实源码是 `'hydro1d': True`——
   `str.replace` 找不到就静默什么都不做，脚本照样正常退出。
   "看着干完了，其实没干"。

**规矩**：`tools/` 只放**可重复运行、幂等、带校验**的工具（`check_*.py` / `gui_smoke.py`
这类）。一次性改写源码的脚本用完就删，不要提交。要改源码就用编辑器改，别用 `.replace()`。

### #33 **合并 PR 之前必须跑一遍三腿**（这次 master 真的崩过）

2026-10-06 那段历史里，PR #14 与 #15 合并产生冲突，留下了对 `regime_trans`
的引用但**没有定义它** → `compute_hydro1d_profile` 直接抛 `NameError`，
一维推算整个跑不了。后来靠 `bf35c65 Fix merge conflict in hydro1d.py crashing
1D calculation` 才补上——那个提交信息自己写明了这一点。

**也就是说 master 在那两次合并之间是坏的。** 本仓库当时已有 200+ 单测，
跑一遍就能发现。所以：**合并前跑三腿**（单测 → `gui_smoke` → `--selftest`），
别指望"能 import 就算过"。

### #34 `compute_hydro1d_profile` 返回的是 **tuple**，不是 list

2026-10-07 起（PR #18 为了拿节点细节加的）：

```python
def compute_hydro1d_profile(...) -> tuple[list[float], list['HydroNode']]:
```

调用方必须解包，`solver.py` 的写法是标准姿势：

```python
line.hydro1d_levels, line.hydro1d_nodes = compute_hydro1d_profile(line, cfg, results,
                                                                  warnings=warnings)
```

⚠ 这是**破坏性**的签名变更——旧调用方会把 tuple 当 list 用。
`tests/` 里有两处故意忽略返回值所以没暴露，别因此以为改签名无所谓。

配套：`ProfileLine.hydro1d_nodes` 的类型标注是 `list[Any]`，
而 `Any` 一度**没有 import**（只 import 了 `Optional`）。当时靠
`from __future__ import annotations`（注解不求值）侥幸不报错，
一旦有人 `typing.get_type_hints()` 就会 `NameError`。已补上 import。

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
