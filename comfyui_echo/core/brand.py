"""Echo 品牌标识的**唯一参数来源**。

logo 本身是矢量文件 `resources/echo_logo.svg`（2026-10-10 依原图重绘，
全部用圆头矩形 / 圆弧 / 圆这类基本图元，没有折线）。

**注意 logo 的形状**：它是**正方形**的 —— 圆角方块 + 居中的 ECHO 字标
（用户 2026-10-10 明确纠正过：「是一个方形然后里边是字标图案」，
不要只画那排字标、也不要自造方标）。参考原图是 `resources/echo_logo.png`
与 `resources/echo_white.ico`，两者的几何完全一致（后者多一圈浅灰描边）。

色值与尺寸常量只在这里定义，Qt 侧 `ui/widgets.LogoBadge` 与
`core/tray.make_icon` 都从这里取，别再各写一份。
"""
from __future__ import annotations

BLUE = "#5cb4d8"        # 字标主色
ORANGE = "#f09858"      # 字标里 O 中心的实心点

LOGO_SVG = "echo_logo.svg"
LOGO_SIZE = 256                 # logo 是正方形，viewBox 256×256
CORNER_RATIO = 54.5 / 256       # 方块圆角半径 / 边长（= 21.3%，由原图左上角边界点拟合圆得到）

# logo 有三种呈现形态，共用 SVG 里同一份字标几何（见 ui/widgets.logo_renderer）：
#   full    = 白色圆角填充 + 描边 + 字标 —— 图标场合（托盘 / exe 图标）
#   outline = 只留那圈描边 + 字标（**默认**）—— 内嵌在有色背景上时用
#             （左上角品牌区 / 关于页）。白底填充压在浅色侧栏上是一块突兀的方块，
#             但**那圈边是 logo 自带的**，要留着（用户 2026-10-10 特意问过
#             「我那个logo不是周围带着一个边的吗，怎么取消了」）。
#   none    = 描边也不要，只剩字标
# 切换方式是把对应的标记块整段摘掉：PySide6 没暴露 QSvgRenderer.setElementVisibility，
# 所以只能走源码标记。两块分开是因为「填充」和「描边」是两件独立的事。
TILE_BG_BEGIN = "<!-- TILE-BG:BEGIN -->"
TILE_BG_END = "<!-- TILE-BG:END -->"
TILE_BORDER_BEGIN = "<!-- TILE-BORDER:BEGIN -->"
TILE_BORDER_END = "<!-- TILE-BORDER:END -->"


def rgba(hex_color: str):
    """`'#rrggbb'` -> `(r, g, b, 255)`（给 PIL 用）。"""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
