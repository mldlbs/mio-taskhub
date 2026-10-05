# 工作状态 · 2026-08-17：打包流程整理 + 绿色版重打

## 一句话
统一打包 spec 为根目录单一副本；新增一键打包脚本 `packaging/build.ps1`；重打绿色版分发包（57 MB）验证通过。

## 变更
1. **统一 spec**：删除冗余副本 `packaging/mio-taskhub.spec`，唯一权威 `mio-taskhub.spec`（三 EXE：hub/mcp/widget + excludes 瘦身 + SPECPATH 绝对路径）。
2. **新增 `packaging/build.ps1`**（UTF-8 with BOM，PS 5.1 必须）：
   - ① `web/npm run build`（node_modules 缺失自动先 install）
   - ② 清空 build/dist
   - ③ `python -m PyInstaller mio-taskhub.spec --noconfirm --clean`
   - ④ 复制 setup-agent.bat/ps1、setup-opencode.bat/ps1、使用说明.txt、workbuddy/taskhub-skill 进 `dist/mio-taskhub/`
   - ⑤ 压缩 `dist/mio-taskhub-绿色版.zip`
3. **HOWTO.md** 打包章节改为引用 build.ps1，维护要点同步（补充 widget exe 与 run_widget.py 入口）。

## 关键约束
- build.ps1 必须存 **UTF-8 with BOM**，否则 PS 5.1 按 ANSI 读中文直接解析失败（已踩坑修复）。
- spec 只有根目录一份，禁止在 packaging/ 下再放副本。
- 打包前前端必须已 build（build.ps1 自动做）；web assets 进 `_internal/web/dist`。
- widget 资源从 `_MEIPASS/web/public/icon.ico` 取；pywebview/pythonnet/pystray/PIL hiddenimports 在 spec。

## 验证
- 产物 `dist/mio-taskhub/`：hub/mcp/widget 三 exe + setup 脚本 + 使用说明 + workbuddy skill。
- MCP 握手 27 个工具（taskhub_status / taskhub_breakdown_idea 均在）。
- hub exe 备用端口 48621 实测：uvicorn 启动、首页 200、WebSocket /ws 连接、DB 初始化正常。
- zip 1061 entries，前端含最新 `index-BaiLnZWE.js`（CPM/甘特改动）。