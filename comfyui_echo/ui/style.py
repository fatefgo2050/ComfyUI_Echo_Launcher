"""全局样式（暖骨白 · 浅色 · 蓝调强调）。

设计规则（依据本地 skill `minimalist-ui`：Premium Utilitarian Minimalism）：
- **暖单色底**：基底用规范给的 warm bone `#f7f6f3`（只留一丝暖意，不是重米黄），
  侧栏比页面深一档、卡片比页面亮一档（卡片直接是**纯白**）。
- **强调色 = 规范里的 Pale Blue**（`#e1f3fe` 底 / `#1f6c9f` 文字）。
  ★2026-10-10 从「Pale Yellow 琥珀金」换过来的：logo 是**青蓝色**字标 + 橙色点，
  琥珀金 UI 和它对不上；换成蓝之后，UI 与 logo 同色系，白色卡片/白底 logo 也不突兀。
- 层次靠 1px 描边而非阴影，禁用渐变 / 霓虹 / 玻璃拟态。
- 正文禁止纯黑，用 `#2f302c`；次要 `#6b6965`；元信息 `#8d8b86`。
  ⚠ 次要 / 元信息这两档**不是规范原值**（规范给 `#787774`）—— 那是给白底网页定的，
  在纯白卡片上只有 4.48，落到侧栏 `#f1efe9` 上只剩 3.89，11px 小字会吃力。
  实测后各压深一档：次要 5.48（白底）/ 4.76（侧栏）、元信息 3.40（白底），都过 WCAG AA。
  **改这两档之后要重跑对比度核验**（`tests/ui_smoke.py` 的 [14] 会按 WCAG 公式把关键
  配对全量核一遍，不达标直接失败）。
- 语义三色取自规范的粉彩深色文字版（Pale Red / Pale Green），全部降饱和。
  ⚠ 规范没给橙色 pastel，WARNING 沿用 `#a8542e` —— 它和 logo 那个橙点 `#f09858`
  是同一色相家族（≈20° vs ≈25°），所以天然协调，不用另造。
- **主操作按钮 = 墨色实心**（2026-10-12 起）：按 minimalist-ui 规范，CTA 用近黑墨色 +
  白字，**不由品牌色充填**；品牌蓝（ACCENT）只保留给「选中 / 开关 / 滑块 / 链接 / pill」
  这些**状态强调**。这样一屏里只有一个"最实"的色块（墨色主操作），色彩不再两头抢。
- **配色已收拢为单一暖调刻度**（2026-10-12）：此前 QSS 有 15 处颜色不落在色板常量内，
  且近似 hover/press 灰（`#efeeea` / `#f2f1ee` / `#f5f4f1` / `#e9e8e4` …）互不统一。
  现已全部归入本文件常量，**QSS 里的颜色 100% 来自色板**（校验脚本：色板外的十六进制 = 0）。
  中性系统一成一条**暖调刻度**（ΔR−B 随深度略增），正文墨色也从偏冷的 `#2f3437` 改为
  暖炭黑 `#2f302c`，与暖骨白底同温，不再"暖底冷字"混用。
- 字阶 11/12/13/14/15，字重 400/500/600/700 四档。
- 间距遵守 4px 网格；圆角分层：容器 12 / 面板 10 / 控件 8 / 迷你 6。
- **★按钮之间的间距分两级**（2026-10-10 定，依据 skill 的邻近性原则）：
  - **卡片内的动作簇**（「库管理」里 查找/安装/删除/列出已安装库 那一排）→ **10px**，
    且换行后行距也是 10（匀齐矩阵）、动作簇与上方字段之间 12（把动作区从表单里划出来）。
    常量在 `env_page.py` 顶部的 `BTN_GAP / BTN_WRAP / BTN_LEAD`。
    原先是 6px：1px 描边 + 6px 圆角的按钮挤在一起像一条粘住的胶带。
  - **页面级工具行**（插件/市场/模型/内核页顶部那条）→ 保持 **6px**：那里密度优先，
    而且一排按钮的宽度预算本来就紧（最小窗口 1100 是按它定的）。
  - 别把「字段行距」和「按钮间距」混成一个数 —— 共用 6px 时，按钮簇看起来就是
    表单又多了一行，这正是用户 2026-10-10 反馈的那个问题。

⚠ 唯一色板来源就是本文件：Python 侧一律 `QColor(style.XXX)`，**不要**再硬编码十六进制。
⚠ 品牌 logo（`resources/echo_logo.svg`）里的蓝 `#5cb4d8` / 橙 `#f09858` 是**品牌资产**，
  不属于主题色板，改主题时不要动它 —— 见 `core/brand.py`。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QProxyStyle, QStyle

# ---- 色板（供 Python 代码取用，保持与 QSS 一致）----
CANVAS = "#f7f6f3"        # 基底 / 页面（规范的 warm bone）
SIDEBAR = "#f1efe9"       # 侧栏（比页面深一档）
SURFACE = "#ffffff"       # 卡片 / 表格 / 输入框（纯白，与 logo 白底同色）
SURFACE_2 = "#faf9f5"     # 次级面（交替行 / 表头带）
HOVER = "#f1efe9"         # 中性 hover 底（列表行 / 页签 / 折叠条）
PRESS = "#e9e6de"         # 中性按下底
DISABLED_BG = "#f3f1ec"   # 控件禁用底
GRID = "#efece6"          # 表格网格线
BORDER = "#e6e3db"        # 结构描边
BORDER_2 = "#dbd8d0"      # 控件描边
BORDER_3 = "#c8c4bb"      # 强描边（hover / 表头下沿）
SCROLL = "#d6d2ca"        # 滚动条滑块
SCROLL_HOVER = "#c1beb5"  # 滚动条滑块 hover
ACCENT = "#1f6c9f"        # 强调色（规范 Pale Blue 的文字色）
ACCENT_HOVER = "#2a7fae"
ACCENT_DARK = "#155a85"
ACCENT_SOFT = "#e1f3fe"   # 低饱和色块（规范 Pale Blue 的底色）
ACCENT_SEL = "#cfe9f8"    # 文本选中（要比 ACCENT_SOFT 再实一点才看得清）
ACCENT_MID = "#8fc4de"    # 滑轨已过部分等中调
INK = "#2f302c"           # 主操作实心底（同正文墨色）
INK_HOVER = "#403d37"     # 主操作实心钮 hover
INK_PRESS = "#22211e"     # 主操作实心钮 pressed
TEXT = "#2f302c"          # 正文 / 墨色（暖炭黑，与暖底同调）
TEXT_2 = "#6b6965"
TEXT_3 = "#8d8b86"
TEXT_UI = "#4a4b45"       # 控件正文（按钮 / 菜单 / 控制台）
TEXT_MUTE = "#b2b0aa"     # 禁用文字
SUCCESS = "#346538"
WARNING = "#a8542e"
DANGER = "#9f2f2d"
DANGER_HOVER = "#b03a37"
DANGER_DARK = "#852321"
DANGER_SOFT = "#fdebec"          # 危险态 pastel 底（规范 Pale Red）
DANGER_SOFT_BORDER = "#cfa9a8"
DANGER_SOFT_PRESS = "#f7dcdc"
DANGER_ON = "#fff4f3"            # 危险实心钮上的文字
MUTED = TEXT_3

# ---- 复选框开关（自绘，见下面的 SwitchStyle）----
SWITCH_OFF = "#cecbc3"            # 关：胶囊轨道
SWITCH_OFF_HOVER = "#c1beb5"
SWITCH_OFF_DISABLED = "#e9e6df"
SWITCH_KNOB = "#ffffff"
SWITCH_KNOB_DISABLED = "#faf9f5"

QSS = """
/* ============ 基底 ============ */
QWidget {
    background: #f7f6f3;
    color: #2f302c;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow { background: #f7f6f3; }
QLabel { background: transparent; }

/* ============ 侧栏 ============ */
#Sidebar {
    background: #f1efe9;
    border-right: 1px solid #e6e3db;
}
#Sidebar QPushButton {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0 8px 8px 0;
    padding: 7px 14px 7px 12px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
    color: #6b6965;
}
#Sidebar QPushButton:hover {
    background: #f7f6f3;
    color: #2f302c;
}
#Sidebar QPushButton:checked {
    background: #e1f3fe;
    border-left: 3px solid #1f6c9f;
    color: #1f6c9f;
    font-weight: 600;
}
/* 侧栏功能分区标题：小号、低对比，只做分组不抢视线 */
QLabel#NavCaption {
    color: #8d8b86;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.2px;
    background: transparent;
    padding: 9px 4px 3px 4px;
}
/* 侧栏底部工具区：「关于」2026-10-10 改成一级页面后，这里已无入口，
   `#NavAux` 规则随之删除（它是「不是页面入口、但仍要看得清」的专用样式）。
   侧栏现在只有导航项 + 分区小标题两种东西。 */
/* 品牌区软件名。字号 14 是量出来的：侧栏 178px 宽，减掉左右内缩 24px、方形 logo 40px、
   间距 8px 后只剩 106px，「ComfyUI Echo」在 15px 会被裁尾、14px 正好（100px）。
   改字号或 logo 尺寸后要重跑 tests/ui_min_size.py。 */
#AppTitle {
    color: #2f302c;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0;
    background: transparent;
}
#AppSubtitle {
    color: #8d8b86;
    font-size: 11px;
    letter-spacing: 1px;
    background: transparent;
}
/* 左上角品牌区的 logo 是自绘控件 `widgets.LogoBadge`（渲染 resources/echo_logo.svg
   矢量图）。**必须显式设成透明**：全局 `QWidget { background: #f7f6f3 }` 会把它刷上一层
   比侧栏（#f1efe9）浅的实心方块 —— 肉眼看起来就是「logo 底下压了一块白板」，
   用户 2026-10-10 反馈过。把白色填充摘掉（FRAME_OUTLINE）之后那块白尤其明显。
   （同类坑：QTabWidget 的内容页、QStackedWidget，见下面「标签页」一节。） */
/* ★「不画背景的容器」统一标记：`widgets.plain_box()` 和自绘的 LogoBadge 都用它。
   全局 `QWidget { background: #f7f6f3 }` 会给**每一个**裸 QWidget 刷上画布色 ——
   落在白色卡片上是一条色带、落在侧栏上是一块比侧栏浅的方块。
   2026-10-10 为此返工过三次（页签内容页 / 品牌区容器 / 注意力卡档位行）。
   判据：**只为布局而存在、不该自己上色**的容器，一律挂 objectName="PlainBox"。 */
QWidget#PlainBox { background: transparent; }
#VersionHint { color: #8d8b86; font-size: 11px; background: transparent; }

/* ============ 警示框（专家模式页顶部的红色提示）============ */
/* ★2026-10-12：原来是一行裸红字；改成 danger pastel 底 + 描边（规范 Pale Red）的框。
   竖向 padding 取 9px，正好和同一行的 #Primary 按钮等高。 */
QFrame#WarnBox {
    background: #fdebec;
    border: 1px solid #cfa9a8;
    border-radius: 10px;
}
QLabel#WarnText {
    color: #9f2f2d;
    font-weight: 600;
    background: transparent;
}

/* ============ 页头 ============ */
/* 没有卡片结构、直接铺工具条+表格的页面（插件/市场/模型/媒体/内核），
   在左上角补一行页头：一个字重 700 的页面名 + 一句低对比说明。
   高度只有一行（约 20px），不跟下面的表格抢空间，但让每个页面都有「标题读法」。 */
QLabel#PageTitle {
    color: #2f302c;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.2px;
    background: transparent;
}
QLabel#PageSub {
    color: #8d8b86;
    font-size: 12px;
    background: transparent;
}

/* ============ 卡片 ============ */
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 12px;
}
QLabel#CardTitle {
    color: #8d8b86;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
    background: transparent;
}

/* 卡片标题行右端的小动作（复制日志 / 复制命令）：低调，不抢卡片标题 */
QPushButton#CardAction {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 12px;
    font-weight: 500;
    color: #8d8b86;
}
QPushButton#CardAction:hover { background: #e1f3fe; color: #1f6c9f; }
QPushButton#CardAction:pressed { background: #cfe9f8; }

/* 表单内分隔线（卡片里分簇用） */
QFrame#Divider {
    background: #e6e3db;
    border: none;
    min-height: 1px;
    max-height: 1px;
}

/* 折叠条：去掉按钮外框，用一条分隔线收口 */
QPushButton#Disclosure {
    background: transparent;
    border: none;
    border-top: 1px solid #e6e3db;
    border-radius: 0;
    padding: 9px 2px;
    text-align: left;
    color: #6b6965;
    font-weight: 600;
}
QPushButton#Disclosure:hover { color: #1f6c9f; background: transparent; }
QPushButton#Disclosure:pressed { background: transparent; }

/* ============ 按钮 ============ */
QPushButton {
    background: #ffffff;
    border: 1px solid #dbd8d0;
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 500;
    color: #4a4b45;
}
QPushButton:hover { background: #f7f6f3; border-color: #c8c4bb; color: #2f302c; }
QPushButton:pressed { background: #efece6; }
QPushButton:focus { border: 1px solid #1f6c9f; }
QPushButton:disabled { color: #b2b0aa; background: #f3f1ec; border-color: #e6e3db; }

QPushButton#Primary {
    /* 主操作 = 墨色实心（minimalist-ui 规范：CTA 用近黑墨色 + 白字，
       不用品牌色；品牌蓝留给"选中 / 开关 / 链接"等状态强调） */
    background: #2f302c;
    border: none;
    color: #ffffff;
    font-weight: 700;
    padding: 9px 22px;
}
QPushButton#Primary:hover { background: #403d37; }
QPushButton#Primary:pressed { background: #22211e; }
QPushButton#Primary:disabled { background: #e9e6de; color: #b2b0aa; }

QPushButton#Danger {
    background: #9f2f2d;
    border: none;
    color: #fff4f3;
    font-weight: 600;
    padding: 9px 22px;
}
QPushButton#Danger:hover { background: #b03a37; }
QPushButton#Danger:pressed { background: #852321; }
QPushButton#Danger:disabled { background: #f3f1ec; color: #b2b0aa; }

/* 「Mini」是卡片内/栏内的小动作按钮。它和「Ghost」现在是同一套形态（透明底 + 1px 描边），
   只是历史叫法不同 —— 统一成一条规则，省得两个 objectName 慢慢漂移成两种观感。
   ⚠ 卡片里原来是一片白底小方块，和卡片叠在一起像贴了一堆膏药；透明底后安静很多。
   需要「小尺寸但仍要显眼」的主操作，用下面的 #MiniPrimary（蓝描边），不要再给 #Mini 加实心底。 */
QPushButton#Mini, QPushButton#Ghost {
    background: transparent;
    border: 1px solid #dbd8d0;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 500;
    color: #6b6965;
}
QPushButton#Mini:hover, QPushButton#Ghost:hover {
    background: #e1f3fe; border-color: #c8c4bb; color: #1f6c9f;
}
QPushButton#Mini:pressed, QPushButton#Ghost:pressed { background: #cfe9f8; }
QPushButton#Mini:disabled, QPushButton#Ghost:disabled {
    color: #b2b0aa; border-color: #e6e3db; background: transparent;
}

/* 「大号 Ghost」：和输入框 / #Primary 并排的表单行动作按钮。
   ⚠ 为什么需要它：`#Ghost` 是 3px 竖向内边距（约 24px 高），而 `#Primary` 是 9px（约 35px）——
   一排里既有主按钮又有幽灵按钮时，**幽灵按钮会明显矮一头**（模型页底部那排就是，
   用户 2026-10-11 反馈「按钮可以适当放大一些」）。同一行想齐高就用这个，
   **别去改 #Ghost 的 padding**（那样所有工具栏的小按钮都会一起变胖）。 */
QPushButton#GhostLg {
    background: transparent;
    border: 1px solid #dbd8d0;
    border-radius: 6px;
    padding: 9px 14px;
    font-size: 12px;
    font-weight: 500;
    color: #6b6965;
}
QPushButton#GhostLg:hover {
    background: #e1f3fe; border-color: #c8c4bb; color: #1f6c9f;
}
QPushButton#GhostLg:pressed { background: #cfe9f8; }
QPushButton#GhostLg:disabled {
    color: #b2b0aa; border-color: #e6e3db; background: transparent;
}

/* 迷你尺寸但做主操作（模式页「保存模式」/ 专家页「恢复默认」）。
   ★2026-10-12：与 #Primary **统一成墨色实心** —— 原来它是一枚淡蓝底 + 蓝描边的
   「第二套主操作」，而 #Primary 已按规范改成墨色，两种主操作语言互相抢。
   品牌蓝（ACCENT）现在只留给「选中 / 开关 / 滑块 / 链接 / pill」等**状态**。
   仍用小 padding，免得一排按钮里它挤掉邻居的字。 */
QPushButton#MiniPrimary {
    padding: 2px 10px;
    font-size: 12px;
    border-radius: 6px;
    border: none;
    background: #2f302c;
    color: #ffffff;
    font-weight: 700;
}
QPushButton#MiniPrimary:hover { background: #403d37; }
QPushButton#MiniPrimary:pressed { background: #22211e; }
QPushButton#MiniPrimary:disabled {
    background: #f3f1ec; color: #b2b0aa; border: none;
}

/* 次要动作统一 ghost 形态：透明底 + 1px 描边，hover 才给淡蓝底。
   一排按钮里只留主操作是实心的，白底块少了，视觉就安静了。
   （规则与上面的 #Mini 合并） */

/* 停止这类「平时禁用的危险动作」：ghost 底 + 危险色文字，不抢启动的戏 */
QPushButton#GhostDanger {
    background: transparent;
    border: 1px solid #dbd8d0;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 600;
    color: #9f2f2d;
}
QPushButton#GhostDanger:hover {
    background: #fdebec; border-color: #cfa9a8; color: #9f2f2d;
}
QPushButton#GhostDanger:pressed { background: #f7dcdc; }
QPushButton#GhostDanger:disabled { color: #b2b0aa; border-color: #e6e3db; background: transparent; }

/* 元信息小标签（版本信息行）：pastel 底 pill —— 规范 Pale Blue 的底 + 文字色 */
QLabel#Pill {
    background: #e1f3fe;
    color: #1f6c9f;
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}

/* 控制台空状态：居中一句话，有日志后隐藏 */
QLabel#ConsoleHint {
    color: #b2b0aa;
    font-size: 13px;
    background: transparent;
}

/* 表格空态：叠在表格视口中央的一句提示（有行时自动隐藏，见 widgets.TableEmptyHint） */
QLabel#EmptyHint {
    color: #8d8b86;
    font-size: 13px;
    background: transparent;
}

/* ============ 输入控件 ============ */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #dbd8d0;
    border-radius: 8px;
    padding: 5px 9px;
    selection-background-color: #cfe9f8;
    selection-color: #2f302c;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {
    border: 1px solid #1f6c9f;
}
QLineEdit:disabled, QComboBox:disabled { color: #b2b0aa; }

/* 微调框内部的 QLineEdit 会再吃一遍上面那条 QLineEdit 规则，渲染成「框里套框」——
   这里把内层抹平，只留 QSpinBox 自己那一道边框 */
QSpinBox QLineEdit {
    border: none;
    background: transparent;
    padding: 0;
    border-radius: 0;
}

/* 「插件」页详情条的两个**只读**文本框（插件名 / 远程地址）：长得像文本、行为像输入框
   —— 能拖选、能 Ctrl+A / Ctrl+C，超宽时自己横向滚（QLabel 只会被裁掉）。所以把输入框
   那道框和底色抹掉。⚠ `:focus` 必须跟着抹，否则会被上面的通用 `QLineEdit:focus` 画回蓝框。 */
QLineEdit#CopyLine {
    background: transparent;
    border: none;
    padding: 0;
    border-radius: 0;
    color: #2f302c;
    font-size: 13px;
}
QLineEdit#CopyLine:focus { border: none; }

QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #c8c4bb;
    border-radius: 8px;
    selection-background-color: #cfe9f8;
    selection-color: #2f302c;
    outline: none;
}
QComboBox QAbstractItemView::item { min-height: 26px; padding: 2px 6px; }

/* ============ 复选框 ============ */
/* ★指示器**不在 QSS 里管** —— 它由下面 `SwitchStyle`（QProxyStyle）自绘成**胶囊开关**
   （胶囊轨道 + 圆形滑块，滑块随开/关左右移动）。
   ⚠ 这里**绝对不能**再出现 `QCheckBox::indicator` 规则：只要 QSS 声明了指示器，
     绘制权就全归 QStyleSheetStyle，自绘开关会被顶掉、退回默认方块（实测过）。
   ⚠ 也**别**指望用 QSS 做开关：`::indicator` 的 image 只能居中贴一张固定图，
     QSS 没有 background-position，滑块动不了 —— 做出来还是「方块变个色」。 */
QCheckBox { spacing: 10px; background: transparent; color: #4a4b45; }
QCheckBox:hover { color: #2f302c; }
QCheckBox:disabled { color: #b2b0aa; }

/* ============ 表格 ============ */
QTableWidget {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 10px;
    gridline-color: #efece6;
    alternate-background-color: #faf9f5;
    outline: none;
}
QTableWidget::item { padding: 6px 4px; }
QTableWidget::item:selected { background: #e1f3fe; color: #1f6c9f; }
QHeaderView::section {
    background: transparent;
    color: #6b6965;
    border: none;
    border-bottom: 1px solid #c8c4bb;
    padding: 8px 6px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}
QTableCornerButton::section { background: transparent; border: none; }

/* ============ 列表 / 树 ============ */
QListWidget, QTreeWidget {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 10px;
    outline: none;
}
QListWidget::item, QTreeWidget::item { padding: 6px 8px; }
/* 列表项留圆角（一行行看得清）；树行必须直角 —— 圆角在树里会露出
   「四个带颜色的角」（行底色在圆角外透出来），很难看 */
QListWidget::item { border-radius: 6px; }
QTreeWidget::item { border-radius: 0; }
QListWidget::item:hover, QTreeWidget::item:hover { background: #f1efe9; }
QListWidget::item:selected, QTreeWidget::item:selected { background: #e1f3fe; color: #1f6c9f; }
/* 分组用的文件夹行（不可选中）：当小标题看 */
QTreeWidget::item:disabled { color: #8d8b86; }

/* ============ 控制台 / 命令预览 ============ */
QPlainTextEdit#Console {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 10px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 12px;
    color: #4a4b45;
    selection-background-color: #cfe9f8;
}
QPlainTextEdit#Preview {
    background: transparent;
    border: none;
    padding: 0;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 12px;
    color: #6b6965;
    selection-background-color: #cfe9f8;
}

/* 「关于」页（一级页面 `about_page.py`）：**卡片栈**（v9 起，模板 = settings_page）。
   页头左对齐；小节标题走 widgets.card() 的 #CardTitle；定义行走 #AboutKey + #AboutVal；
   条目标记走 #Bullet。正文段落全是 QLabel + wordWrap，字体/颜色走这里；
   **含 `行内代码` 的段落是 Qt 富文本、颜色由 about_page._rich() 从色板常量上色**，
   QSS 管不到那一段，改主题时两边都要对。 */
QLabel#AboutTitle {
    color: #2f302c;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.2px;
    background: transparent;
}
QLabel#AboutText { color: #2f302c; font-size: 13px; background: transparent; }
QLabel#AboutNote { color: #6b6965; font-size: 12px; background: transparent; }
QLabel#AboutKey  { color: #2f302c; font-size: 12px; font-weight: 600; background: transparent; }
/* 定义行的「值」列（数据源 / 运行环境），与名称列 #AboutKey 配对。
   值允许换行（例如「镜像源」那行很长），护栏只要求**不被裁**，见 ui_min_size.py。 */
QLabel#AboutVal  { color: #6b6965; font-size: 12px; background: transparent; }
/* 「关于」页条目的「●」标记。⚠ 这条是 v9 新补的：此前 about_page 一直在设
   objectName="Bullet"，但 QSS 里**一条规则都没有** → 圆点吃默认样式（与正文同色），
   整页看不出条目标记。补成强调色，跟正文形成层次。 */
QLabel#Bullet {
    color: #1f6c9f;
    font-size: 9px;
    background: transparent;
}

/* ============ 版本管理对话框 ============ */
/* 路径信息行：等宽 + 低对比，方便核对盘符与目录层级 */
QLabel#PathLine {
    color: #6b6965;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 12px;
    background: transparent;
}
/* 当前版本：整个对话框最该被看见的一行，给最重的字重 */
QLabel#CurrentVer {
    color: #2f302c;
    font-size: 14px;
    font-weight: 700;
    background: transparent;
    padding: 2px 0;
}
/* 更新日志面板：用正文字体（非控制台等宽体），可选中复制 */
QPlainTextEdit#LogView {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 10px;
    font-size: 12px;
    color: #4a4b45;
    selection-background-color: #cfe9f8;
}

/* ============ 菜单 / 提示 ============ */
QMenu {
    background: #ffffff;
    border: 1px solid #c8c4bb;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 24px 6px 16px;
    border-radius: 6px;
    color: #4a4b45;
}
QMenu::item:selected { background: #e1f3fe; color: #1f6c9f; }
QMenu::item:disabled { color: #b2b0aa; }
QMenu::separator { height: 1px; background: #e6e3db; margin: 5px 8px; }
QToolTip {
    background: #ffffff; color: #2f302c;
    border: 1px solid #c8c4bb; border-radius: 6px;
    padding: 5px 8px; font-size: 12px;
}

/* ============ 可滚动容器（环境页左栏） ============ */
/* 只要滚动能力，不要它自带的底色和边框 */
QScrollArea#PlainScroll { background: transparent; border: none; }
QScrollArea#PlainScroll > QWidget > QWidget { background: transparent; }

/* ============ 标签页 ============ */
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #e6e3db;
    border-radius: 10px;
    top: -1px;
}
/* 卡片内的页签：卡片本身已有描边，这里去掉重复的框 */
QTabWidget#ParamTabs::pane {
    background: transparent;
    border: none;
    top: 1px;
}
/* ★页签的内容区必须显式刷成卡片色：全局 `QWidget { background: #f7f6f3 }` 会把
   QTabWidget 内部的 QStackedWidget **和**每个页签的内容页这两层普通 QWidget 都刷成
   画布色。落在白色卡片上就是一块比卡片深一档的硬边矩形 —— 看起来像
   「页签栏下面压了一条色带」，而且它紧贴页签栏，第一行控件（常用页的「fp16 累加加速」、
   网络页的「HuggingFace 国内镜像」）的文字像跟上边粘住了。
   两层都要盖住：只写内容页的话，透出来的还是 QStackedWidget 的画布色。
   页签内容页统一挂 objectName="ParamPage"（见 home_page._form 的三次调用处）。 */
QTabWidget#ParamTabs > QStackedWidget,
QWidget#ParamPage { background: #ffffff; }
/* 页签栏与内容之间还留了 1px 的缝（`::pane { top: 1px }`），不盖住会显一条淡色细线 */
QTabWidget#ParamTabs { background: #ffffff; }
QTabWidget#ParamTabs QTabBar::tab {
    padding: 5px 13px;
    font-size: 12px;
}
QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
}
QTabBar::tab {
    background: transparent;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 16px;
    margin-right: 2px;
    color: #6b6965;
    font-weight: 500;
}
QTabBar::tab:hover { background: #f3f1ec; color: #2f302c; }
QTabBar::tab:selected {
    background: #ffffff;
    border-color: #e6e3db;
    color: #1f6c9f;
    font-weight: 600;
}

/* ============ 滑块 ============ */
QSlider::groove:horizontal {
    height: 4px;
    background: #e6e3db;
    border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #8fc4de; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #1f6c9f;
    border: none;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #2a7fae; }
QSlider::groove:vertical { width: 4px; background: #e6e3db; border-radius: 2px; }
QSlider::handle:vertical {
    background: #1f6c9f;
    border: none;
    height: 14px;
    margin: 0 -5px;
    border-radius: 7px;
}

/* ============ 滚动条 ============ */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #d6d2ca; border-radius: 5px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #c1beb5; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #d6d2ca; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: #c1beb5; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ============ 状态栏 / 分割线 ============ */
QStatusBar {
    background: #f1efe9; color: #8d8b86;
    border-top: 1px solid #e6e3db;
    font-size: 12px;
}
QSplitter::handle { background: #e6e3db; }
QSplitter::handle:hover { background: #c8c4bb; }

/* ============ 右上角窗口按钮（无标题栏，浮在内容之上） ============ */
QPushButton#TitleBtn, QPushButton#TitleClose {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #6b6965;
    font-size: 13px;
    padding: 0;
}
QPushButton#TitleBtn:hover { background: #e6e3db; color: #2f302c; }
QPushButton#TitleBtn:pressed { background: #dbd8d0; }
QPushButton#TitleClose:hover { background: #9f2f2d; color: #fff4f3; }
QPushButton#TitleClose:pressed { background: #852321; }
"""

# ---- 自绘控件样式（胶囊开关）----
class SwitchStyle(QProxyStyle):
    """把 QCheckBox 的指示器自绘成**胶囊开关**（胶囊轨道 + 圆形滑块）。

    为什么不用 QSS 做：`::indicator` 只能改尺寸 / 填色 / 贴一张**位置固定**的图，
    **没法让滑块随状态左右移动**（image 只能居中画，QSS 也没有 background-position），
    做出来仍是「一个方块点一下就变个色」（用户 2026-10-10 反馈要换掉这种）。
    颜色一律从上面的色板常量取 → 换主题自动跟随，不像贴图那样把颜色焊死。

    ⚠ 两条必须同时满足，否则不生效（都是实测出来的）：
      1. QSS 里**不能有任何 `QCheckBox::indicator` 规则** —— 只要声明了，绘制权就全归
         QStyleSheetStyle，这里的 drawPrimitive 根本不会被调用。
      2. 尺寸必须从 `pixelMetric(PM_IndicatorWidth/Height)` 走 —— QSS 生效时
         QStyleSheetStyle 自己算 SE_CheckBoxIndicator 的矩形，会**绕过** subElementRect。
    """

    W, H = 38, 20          # 胶囊轨道尺寸（逻辑像素）
    KNOB_PAD = 2           # 滑块与轨道内边距

    def pixelMetric(self, m, opt=None, w=None):      # noqa: N802 (Qt 命名)
        if m == QStyle.PM_IndicatorWidth:
            return self.W
        if m == QStyle.PM_IndicatorHeight:
            return self.H
        if opt is not None:
            return super().pixelMetric(m, opt, w)
        return super().pixelMetric(m)

    def drawPrimitive(self, element, option, painter, widget=None):   # noqa: N802
        if element != QStyle.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return
        r = QRectF(option.rect)
        on = bool(option.state & QStyle.State_On)
        enabled = bool(option.state & QStyle.State_Enabled)
        hover = bool(option.state & QStyle.State_MouseOver)
        if not enabled:
            track = QColor(ACCENT_SOFT if on else SWITCH_OFF_DISABLED)
            knob = QColor(SWITCH_KNOB_DISABLED)
        elif on:
            track = QColor(ACCENT_HOVER if hover else ACCENT)
            knob = QColor(SWITCH_KNOB)
        else:
            track = QColor(SWITCH_OFF_HOVER if hover else SWITCH_OFF)
            knob = QColor(SWITCH_KNOB)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(r, r.height() / 2.0, r.height() / 2.0)
        d = r.height() - 2.0 * self.KNOB_PAD
        x = (r.right() - self.KNOB_PAD - d) if on else (r.left() + self.KNOB_PAD)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, r.top() + self.KNOB_PAD, d, d))
        painter.restore()


def apply(app):
    """把主题装到 QApplication 上：QProxyStyle（胶囊开关）+ 全局 QSS。

    ⚠ **所有入口都走这个函数**（main.py / 各测试与截图脚本）—— 不要再各自
      `app.setStyle("Fusion")` + `app.setStyleSheet(QSS)`：漏掉 QProxyStyle 的那处
      复选框会静默退回方块外观，很难发现。
    顺序也重要：先 setStyle（SwitchStyle 包住 Fusion），再 setStyleSheet
    （QSS 再包一层 QStyleSheetStyle，最终链路 = QSS → SwitchStyle → Fusion）。
    """
    from PySide6.QtWidgets import QStyleFactory
    app.setStyle(SwitchStyle(QStyleFactory.create("Fusion")))
    app.setStyleSheet(QSS)
