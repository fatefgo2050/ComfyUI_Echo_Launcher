"""统一的确认 / 提示弹窗。

用户要求：
  - 这些「小窗口」**不要那个蓝色问号图标**；
  - 标题栏只显示短标题，**不要「- ComfyUI Echo 启动器 v0.1.0」后缀**
    （那串是 Windows 用 `applicationDisplayName` 拼上去的，`main.py` 已不再设置）。

用法：页面里把导入换成

    from .dialogs import MessageBox as QMessageBox

原来的 `QMessageBox.question/information/warning/critical(...)` 调用一行都不用改。
`MessageBox` 继承自 `QMessageBox`，所以 `QMessageBox.Yes / No / Ok / Cancel …` 这些
枚举常量照样可用。
"""
from PySide6.QtWidgets import QMessageBox


class MessageBox(QMessageBox):
    """`QMessageBox` 的替身：静态便捷方法一律**不带图标**。"""

    @staticmethod
    def _run(parent, title, text, buttons, default_button):
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.NoIcon)          # 去掉问号 / 感叹号等图标
        box.setWindowTitle(title or "")
        box.setText(text or "")
        box.setStandardButtons(buttons)
        if default_button != QMessageBox.NoButton:
            box.setDefaultButton(default_button)
        box.exec()
        clicked = box.clickedButton()
        # 关掉窗口（点 X）时 clickedButton() 为 None -> 当作「没确认」
        return box.standardButton(clicked) if clicked else QMessageBox.NoButton

    @staticmethod
    def information(parent, title, text, buttons=QMessageBox.Ok,
                    defaultButton=QMessageBox.NoButton):
        return MessageBox._run(parent, title, text, buttons, defaultButton)

    @staticmethod
    def warning(parent, title, text, buttons=QMessageBox.Ok,
                defaultButton=QMessageBox.NoButton):
        return MessageBox._run(parent, title, text, buttons, defaultButton)

    @staticmethod
    def critical(parent, title, text, buttons=QMessageBox.Ok,
                 defaultButton=QMessageBox.NoButton):
        return MessageBox._run(parent, title, text, buttons, defaultButton)

    @staticmethod
    def question(parent, title, text,
                 buttons=QMessageBox.Yes | QMessageBox.No,
                 defaultButton=QMessageBox.NoButton):
        return MessageBox._run(parent, title, text, buttons, defaultButton)
