"""Launch a Windows-only credential dialog, keeping the value outside HTTP/UI state."""

import re
import subprocess
import sys
from pathlib import Path

from app.core.errors import AppError


def open_key_dialog(variable: str) -> dict[str, str]:
    if not re.fullmatch(
        r"DEEPSEEK_API_KEY|TAVILY_API_KEY|RESEARCH_MODEL_KEY_[A-Z0-9_]{1,80}", variable
    ):
        raise AppError("invalid_key_variable", "密钥变量名无效。", 422)
    if sys.platform != "win32":
        raise AppError("native_dialog_unavailable", "此配置窗口需要在 Windows 本机打开。", 409)
    root = Path(__file__).resolve().parents[2]
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts/configure_key.ps1"),
                "-Variable",
                variable,
            ],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError:
        raise AppError(
            "native_dialog_failed", "无法打开本机密钥窗口，请检查 Windows 环境。", 503
        ) from None
    return {"status": "opened", "message": "请在 Windows 本机窗口输入；保存后重启平台生效。"}
