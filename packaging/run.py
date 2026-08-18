# mio-taskhub 统一入口（单 EXE 分派）
# 用法:
#   mio-taskhub.exe            → hub（双击启动 + 自动开浏览器）
#   mio-taskhub.exe mcp        → MCP 服务端（agent 经 stdio 调用）
#   mio-taskhub.exe widget     → 置顶浮动任务中心面板
import os
import sys


def _stdio():
    """windowed(console=False) 下 stdout/stderr 可能为 None，重绑到文件日志。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    data_dir = os.path.join(os.path.expanduser("~"), ".mio_taskhub")
    os.makedirs(data_dir, exist_ok=True)
    log = os.path.join(data_dir, "console.log")
    f = open(log, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "hub").lower()
    if mode == "mcp":
        from mio_taskhub.mcp_server import main as mcp_main

        mcp_main()
        return
    _stdio()
    if mode == "widget":
        from run_widget import main as widget_main

        widget_main()
        return
    from run_hub import main as hub_main

    hub_main()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass