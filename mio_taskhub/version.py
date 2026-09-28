# -*- coding: utf-8 -*-
"""鍗曚竴鐗堟湰婧?+ 瀹夎鐩綍瑙ｆ瀽銆?
`__version__` 鏄敮涓€鏉冨▉鐗堟湰鍙凤細鎵撳寘鏃剁敱 packaging/build.ps1 瑕嗗啓锛?杩愯鏃惰 FastAPI(app version) 涓?update 瀛愭ā鍧楀叡鍚屽紩鐢ㄣ€?"""
import sys
from pathlib import Path

__version__ = "0.5.0"


def is_frozen() -> bool:
    """鏄惁杩愯鍦?PyInstaller 鎵撳寘浜х墿涓€?""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """绋嬪簭鐩綍锛歠rozen 鈫?exe 鎵€鍦ㄧ洰褰曪紱寮€鍙戞€?鈫?浠撳簱鏍广€?""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
