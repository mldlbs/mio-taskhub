# Build / Repack / Verify Checklist (mio-taskhub)

Always-on rules for building, packaging, and verifying the installer + payload.

## Clean rebuild sequence
1. `rm -rf build build_installer dist_installer dist/mio-taskhub-v3`
2. Build main program: `.venv/Scripts/python.exe -m PyInstaller mio-taskhub.spec --distpath dist/mio-taskhub-v3`
3. Copy `uninstall.exe` (from `dist_installer/`) into `dist/mio-taskhub-v3/`
4. Ensure payload root has: `TaskHub.exe` (main), `uninstall.exe`, `_internal/`, `workbuddy/`, `agent_wrapper.py`, etc.
5. Build zip: `.venv/Scripts/python.exe scripts/make_zip.py dist/mio-taskhub-v3 dist/mio-taskhub-win64.zip`
6. Make bundle: `.venv/Scripts/python.exe scripts/make_setup.py --setup dist_installer/mio-taskhub-setup.exe --payload dist/mio-taskhub-win64.zip --out dist/mio-taskhub-installer/mio-taskhub-setup.exe --verify`
7. Verify bundle: `.venv/Scripts/python.exe scripts/check_installer_bundle.py` → must return `[OK]`

## Build pitfalls
- **Use `.venv/Scripts/python.exe`, never `sys.executable`** (can point to wrong interpreter, causes `No module named PyInstaller` or binary errors).
- **Rebuild `dist_installer` first** when changing `APP_EXE_NAME` or `SHORTCUT_NAME` — the installer embeds updated `paths.py` references.
- **Payload zip must use flat layout** (no `mio-taskhub-v3/` prefix inside zip) — `make_zip.py` handles stripping.
- **Bundle `setup.exe` + payload zip digest must match** — `check_installer_bundle.py` verifies offset + size consistency.
- **Process name change (`TaskHub.exe`) requires full rebuild** — spec `name=`, `APP_EXE_NAME`, payload zip contents, `run_widget.py`, and all reference code updated together. Partial update = broken install.

## Verification commands
- `python -c "import zipfile; z=zipfile.ZipFile('dist/mio-taskhub-win64.zip'); print('TaskHub.exe:', 'TaskHub.exe' in z.namelist(), 'uninstall.exe:', 'uninstall.exe' in z.namelist())"`
- End-to-end silent install + uninstall: `setup.exe --silent --dir <dir>`, then `setup.exe --uninstall --silent --dir <dir>`; verify install dir removed and no `mio_embedded_*` temp leaks.
