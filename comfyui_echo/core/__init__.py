"""适配层：把 echo_launcher 功能层包装成 UI 期望的接口。

模块划分（与 UI 的导入一一对应）：
    config.py    配置读写（嵌套视图 <-> Echo 扁平配置）
    paths.py     便携包 / ComfyUI 路径发现与校验
    core_version.py  ComfyUI 内核（本体仓库）版本查看与切换 —— 「版本管理」对话框用
    process.py   ComfyUI 进程启动停止（Echo ComfyProcess + Qt 信号）
    plugins.py   插件扫描 / 更新 / 安装 / 启停 / 模式应用
    nodemap.py   节点映射与工作流识别
    pipenv.py    python_embeded 的 pip 环境维护
    attention.py 注意力后端四档 + 解释器级 shim 接管
    rescue.py    救援还原点
"""
