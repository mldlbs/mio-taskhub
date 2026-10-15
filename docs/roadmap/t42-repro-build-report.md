# 可复现构建验证报告（T4.2 / G1·M3）

> 取证日：2026-10-14 · 工具：`scripts/repro_build.py` · 环境：本机 Windows

## 1. 方法

同一 commit（HEAD `d916a20`，tag v0.6.0）连续两次**干净构建**（`PyInstaller --clean`，
前端 `web/dist` 已构建），对比产物逐文件 SHA256：

```powershell
python -m PyInstaller mio-taskhub.spec --noconfirm --clean --distpath dist_b1 --workpath build_b1
python -m PyInstaller mio-taskhub.spec --noconfirm --clean --distpath dist_b2 --workpath build_b2
python scripts/repro_build.py compare      dist_b1/mio-taskhub-v3 dist_b2/mio-taskhub-v3   # 原始
python scripts/repro_build.py compare-norm dist_b1/mio-taskhub-v3 dist_b2/mio-taskhub-v3   # 归一化
```

## 2. 结果

| 对比模式 | 一致 | 差异 | 可复现 |
|---|---|---|---|
| **原始逐字节** | 805 / 807 | **2** | 否（可解释） |
| **归一化**（剥离嵌入元数据） | **807 / 807** | **0** | **是** |

### 差异文件与原因（可解释）

| 文件 | 差异性质 | 根因 |
|---|---|---|
| `mio-taskhub.exe` | 5 字节（3 处） | PE `TimeDateStamp`（`pe_off+8`）+ PE `CheckSum`（`pe_off+0x58`）+ 归档头嵌入的同一时间戳副本；两次构建间隔 65s |
| `_internal/base_library.zip` | zip 元数据 | zip 成员 mtime 不同；**155 个成员内容逐字节一致**（`compare-zip` 验证） |

**结论**：**产物内容 100% 可复现**；仅嵌入构建时刻的元数据（PE 时间戳/校验和、zip mtime）
不同，属 PyInstaller 已知非确定性，**可解释**。归一化后 807/807 完全一致。

## 3. Release 资产校验

| 项 | 值 |
|---|---|
| 产物 | `dist/mio-taskhub-win64.zip` |
| 大小 | 36.9 MB |
| **SHA256** | `9c56ab763d2ed1196ba3dd2dbb0d493206ca3d976fb1dbd4419fd29a1f8eea35` |
| 校验方式 | `Get-FileHash dist\mio-taskhub-win64.zip -Algorithm SHA256` |

> 构建后产物/资产**不入库**（`dist/` 在 `.gitignore`）；Release 时以 SHA256 校验，见 `publish-github-release` 经验。

## 4. 判定

- **同 commit 两次构建产物一致（可对比）**：内容一致（归一化 807/807），元数据差异可解释 ✅
- **构建说明清晰**：本报告 + `build.ps1` 注释 + `repro_build.py` 用法 ✅
- **Release 资产有 SHA256 校验**：`9c56ab76…` ✅
- **失败场景**：无不可解释差异；`dist_b1`/`dist_b2`/`build_b1`/`build_b2` 为临时目录（不入库）✅

## 5. 复现命令

```powershell
# 生成产物清单（逐字节）
python scripts/repro_build.py manifest dist/mio-taskhub-v3

# 生成归一化清单（剥离 PE 时间戳 / zip mtime）
python scripts/repro_build.py manifest-norm dist/mio-taskhub-v3

# 对比两次构建（原始 / 归一化）
python scripts/repro_build.py compare      <dir_a> <dir_b>
python scripts/repro_build.py compare-norm <dir_a> <dir_b>
```

> **诚实边界**：本机两次构建；3 台外部干净机器验证（M3）需人工执行，可复用 `coldstart_timing.py`（T3.5）。
