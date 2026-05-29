"""
FactorFlow API 服务启动脚本
"""
import uvicorn
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    reload_enabled = os.getenv("FACTORFLOW_API_RELOAD", "0").lower() in {"1", "true", "yes"}

    print("=" * 50)
    print("启动 FactorFlow API 服务...")
    print("=" * 50)
    print("API地址: http://localhost:8000")
    print("API文档: http://localhost:8000/docs")
    print(f"自动重载: {'开启' if reload_enabled else '关闭'}")
    print("按 Ctrl+C 停止服务")
    print("=" * 50)

    uvicorn_kwargs = {
        "app": "backend.api.main:app",
        "host": "0.0.0.0",
        "port": 8000,
        "reload": reload_enabled,
        "log_level": "info",
    }
    if reload_enabled:
        uvicorn_kwargs["reload_dirs"] = ["backend"]

    uvicorn.run(
        **uvicorn_kwargs,
    )
