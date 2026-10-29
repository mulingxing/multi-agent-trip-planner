"""高德MCP子进程的独立依赖环境与启动命令。"""

from pathlib import Path
import shutil
import sys


def build_amap_mcp_command() -> list[str]:
    """在uvx隔离环境中运行高德服务，不修改项目虚拟环境的依赖。"""
    scripts_dir = Path(sys.executable).resolve().parent
    uvx_path = next(
        (path for name in ("uvx.exe", "uvx") if (path := scripts_dir / name).is_file()),
        None,
    )
    uvx = str(uvx_path) if uvx_path else shutil.which("uvx")
    if not uvx:
        raise RuntimeError("未找到uvx，请在项目虚拟环境中安装uv后重启后端")

    # amap-mcp-server 0.1.11锁定mcp 1.8.1，后者使用Pydantic旧版私有API。
    # 单独约束子进程依赖，避免uvx解析到不兼容的新版Pydantic。
    return [
        uvx,
        "--python", sys.executable,
        "--with", "pydantic==2.11.10",
        "--with", "pydantic-settings==2.10.1",
        "--from", "amap-mcp-server==0.1.11",
        "amap-mcp-server",
    ]
