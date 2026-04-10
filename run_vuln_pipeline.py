#!/usr/bin/env python3
"""
兼容入口脚本（已拆分模式）。

推荐直接使用：
- run_vuln_local.py    本地模式
- run_vuln_docker.py   Docker 模式
- run_vuln_issue.py    GitHub Issue 模式（委托 Build-Agent clone）

保留本脚本是为了兼容历史命令：
    python3 run_vuln_pipeline.py <target> [--docker] ...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vuln_pipeline_core import detect_target, run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="运行多 Agent 漏洞修复流水线（兼容入口）")
    parser.add_argument("target", help="本地路径、GitHub 仓库 URL 或 GitHub Issue URL")
    parser.add_argument("-t", "--task", default="", help="覆盖 Fixer 阶段任务描述")
    parser.add_argument("--docker", action="store_true", help="在 Docker 中执行三阶段")
    parser.add_argument("-m", "--model", default="", help="指定模型（例如 deepseek/deepseek-reasoner）")
    parser.add_argument("--timeout", type=int, default=900, help="每阶段超时时间（秒）")
    parser.add_argument("--base-image", default="", help="基础 Docker 镜像名（Issue + Docker 模式必填）")
    args = parser.parse_args()

    target_type, _ = detect_target(args.target)
    allow_issue_delegate = target_type == "issue"
    return run_pipeline(
        script_path=Path(__file__),
        target=args.target,
        docker=args.docker,
        task_override=args.task,
        model_override=args.model,
        timeout_sec=args.timeout,
        allow_issue_delegate=allow_issue_delegate,
        base_image=args.base_image,
    )


if __name__ == "__main__":
    raise SystemExit(main())

