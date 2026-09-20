"""core：纯计算层。

约束：本包内除 reader.py（xlsx 解析）与 exporter.py 的 xlsx 分支外，
不得引入任何第三方依赖，也不得 import PySide6。

之所以不用 numpy 向量化：要与 MATLAB 达成 <1e-9 一致，浮点累加顺序必须一致，
向量化会改变顺序引入末位差异。本项目的计算量在毫秒级，无性能压力。
"""
