"""Build from a clean source copy so stale build/ files cannot re-enter the wheel.

不从旧 build/ 打包，而是复制允许的源码到临时目录构建。verify_wheel
再核对静态资源、命令入口和禁止携带的私人文件；需要网络获取构建依赖时
由 pip 处理，与调用模型无关。
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = ("cli", "server", "storage", "mcp_server", "extractor")


def verify_wheel(path):
    """检查静态资源、入口和禁止打包的私有/退役文件，失败抛出异常。"""
    with ZipFile(path) as wheel:
        names = set(wheel.namelist())
        forbidden = {"relationship_agent/" + name + ".py" for name in RETIRED_MODULES}
        unexpected = [
            name
            for name in names
            if name in forbidden
            or name.startswith("relationship_agent/web/")
            or any(
                part in (".env", "memory", "backups", "outputs", "__pycache__")
                for part in Path(name).parts
            )
            or name.endswith((".sqlite3", ".sqlite", ".db"))
        ]
        if unexpected:
            raise ValueError(f"Retired/private artifacts in wheel: {unexpected}")
        for name in ("app.js", "index.html", "style.css"):
            if "relationship_agent/persona_web/" + name not in names:
                raise ValueError(f"Missing Persona UI asset: {name}")
        entry_path = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entries = wheel.read(entry_path).decode()
        for entry, target in {
            "relationship-agent": "relationship_agent.beta.app:main",
            "relationship-agent-web": "relationship_agent.beta.app:main",
            "persona-ai": "relationship_agent.beta.app:main",
            "relationship-agent-mcp": "relationship_agent.beta.mcp_server:main",
        }.items():
            if f"{entry} = {target}" not in entries:
                raise ValueError(f"Wrong installed entry point: {entry}")


def main():
    """从源码隔离副本构建 wheel，检查通过后才复制到输出目录。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="persona-build-") as temporary:
        # 干净副本不包含旧 build/egg-info，防止退役模块被构建缓存带回安装包。
        source = Path(temporary) / "source"
        source.mkdir()
        for name in ("pyproject.toml", "README.md"):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(
            ROOT / "src" / "relationship_agent",
            source / "src" / "relationship_agent",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        wheels = Path(temporary) / "wheels"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(wheels),
                str(source),
            ],
            check=True,
        )
        wheel = next(wheels.glob("relationship_agent-*.whl"))
        # 构建成功不代表内容正确；检查资源和入口后才把文件交给发布流程。
        verify_wheel(wheel)
        target = output / wheel.name
        shutil.copy2(wheel, target)
        print(f"Clean wheel verified: {target}")


if __name__ == "__main__":
    main()
