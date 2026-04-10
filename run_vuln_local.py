#!/usr/bin/env python3
"""
本地模式入口：在宿主机直接执行三阶段。

支持输入：
1) 本地仓库路径
2) GitHub 仓库 URL（先在宿主机 clone，再执行）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vuln_pipeline_core import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="本地模式运行多 Agent 漏洞修复流水线")
    parser.add_argument("target", help="本地路径或 GitHub 仓库 URL")
    parser.add_argument("-t", "--task", default="", help="覆盖 Fixer 阶段任务描述")
    parser.add_argument("-m", "--model", default="", help="指定模型（例如 deepseek/deepseek-reasoner）")
    parser.add_argument("--timeout", type=int, default=900, help="每阶段超时时间（秒）")
    parser.add_argument("--base-image", default="", help="本地模式忽略该参数（为统一入口保留）")
    args = parser.parse_args()
    return run_pipeline(
        script_path=Path(__file__),
        target=args.target,
        docker=False,
        task_override=args.task,
        model_override=args.model,
        timeout_sec=args.timeout,
        allow_issue_delegate=False,
        base_image=args.base_image,
    )


if __name__ == "__main__":
    raise SystemExit(main())
