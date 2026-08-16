# widget 系统托盘驻留

日期：2026-08-15
状态：已确认（brainstorming 结论）

## 背景

mio-taskhub 浮动任务中心面板（`packaging/run_widget.py`，pywebview 置顶窗口）目前**关闭即退出**。用户希望它能驻留 Windows 系统托盘：关窗隐藏、托盘图标可主动打开面板。

## 方案

用 `pystray`（0.19.5）+ `PIL`（已装）提供系统托盘图标；用 pywebview 的 `closing` 事件拦截关闭动作，改为隐藏而非销毁。

```
启动 widget
   │
   ├─ 创建 pywebview 窗口（置顶，现有逻辑）
   ├─ 注册 window.events.closing → 返回 False（取消关闭）→ 改 window.hide()
   └─ 启动 pystray 托盘图标（icon = icon.ico）
         ├─ 单击/双击图标 → window.show()（打开面板）
         └─ 菜单：显示面板 / 退出
               └─ 退出 → icon.stop() + window.destroy() + 退出进程
```

## 实现要点

### `packaging/run_widget.py` 改造

1. **托盘图标**：`pystray.Icon('mio-taskhub', image=PIL.Image.open(ICO), title='mio-taskhub')`。
   - `ICO` 沿用现有 `_res_icon()`（源码/打包双路径解析）。
   - `on_activate`（单击）：调 `window.show()`；若窗口已销毁则重建。
   - 菜单：`显示面板`（`window.show()`）、`退出`（`icon.stop()` + `window.destroy()` + 进程结束）。
2. **拦截关闭**：
   ```python
   def _on_closing():
       w.hide()   # 隐藏而非销毁
       return False  # 取消默认关闭
   w.events.closing += _on_closing
   ```
   - 注意：pywebview Windows 后端 `closing` 事件返回 False 是否真的能取消——若后端不支持返回值拦截，则用 `hide` + 事件不 destroy 的变通。需实测确认（见下）。
3. **退出路径**：托盘菜单「退出」是唯一真正退出方式；托盘图标存在期间窗口 hide 不 destroy，因此 `on_activate` 不需要重建窗口（`window.show()` 即可）。

### 启动流程

- 窗口先创建（`webview.create_window`），`webview.start()` 启动 GUI 循环后：
  - 注册 `closing` 拦截。
  - `pystray.Icon.run()` 在独立线程跑托盘（`threading.Thread(daemon=True)`）。
  - 托盘回调（`window.show()`/`window.destroy()`）从托盘线程调 pywebview 窗口方法——pywebview 窗口方法跨线程调用是安全的（内部线程桥）。
  - 推荐方案 A（pystray 独立线程 + webview 主线程 GUI），实测原型确认：`webview.start()` 阻塞正常、窗口创建成功、closing 事件可注册。完整交互（关窗隐藏/托盘点开/退出）需人工 GUI 验证。

### 退出语义

- 托盘菜单「退出」是唯一真正退出方式：`icon.stop()` 停托盘线程 + `window.destroy()` 销毁窗口 + `webview.start()` 返回后进程自然结束。
- 关窗（X）→ `closing` 拦截 → `window.hide()`（窗口隐藏，进程存活，托盘仍在）。

### 打包

- `mio-taskhub.spec` / `packaging/mio-taskhub.spec` 的 widget EXE hiddenimports 加 `pystray`、`PIL`。
  - **关键**：主 spec 的 `excludes` 含 `PIL`——widget 的 Analysis 用独立 excludes，需去掉 `PIL` 或单独为 widget 保留（widget 需要 PIL 生成托盘图标）。建议 widget Analysis 用不含 `PIL` 的 excludes。
  - 打包后验证托盘图标正常显示。

## 错误处理

| 场景 | 行为 |
|---|---|
| 托盘图标加载失败（ICO 缺失） | 记日志，降级为普通窗口（不驻留托盘，行为同现状） |
| pystray 未安装 | import 失败时降级为现状（无托盘，关窗即退出） |
| 窗口已 destroy 后点托盘 | 重建窗口 |
| hub 未运行 | 现状：弹 MessageBox 提示后退出 |

## 不做的事（YAGNI）

- 不做托盘图标右键自定义菜单扩展（仅「显示面板/退出」）。
- 不做单实例锁（同一窗口可重复打开——现状如此）。
- 不做开机自启。
- 不做 hub 一并托盘化（hub 是服务，保持现状）。

## 测试与验证

- 源码模式：`python packaging/run_widget.py` → 窗口出现 → 关窗后进程仍存活（托盘在）→ 点托盘图标窗口重现 → 托盘菜单退出进程结束。
- 打包模式：重打 widget EXE，双击验证托盘。
- Playwright 不适用（托盘是系统级）；用手动 + 进程检查验证（关闭窗口后 `Get-Process` 确认进程仍在，托盘图标操作后进程退出）。
