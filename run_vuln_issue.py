#!/usr/bin/env python3
"""
GitHub Issue 模式入口。

该模式不会在外层脚本提前 clone；
只把 Issue 信息交给 Build-Agent，让其负责 clone 与环境准备。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证无论从哪个工作目录启动，都能导入同目录下的 vuln_pipeline_core.py（WSL/跨盘符时常见）
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vuln_pipeline_core import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub Issue 模式运行多 Agent 漏洞修复流水线")
    parser.add_argument("issue_url", help="GitHub Issue URL（例如 https://github.com/mruby/mruby/issues/5676）")
    parser.add_argument("--docker", action="store_true", help="在 Docker 中执行三阶段")
    parser.add_argument("-t", "--task", default="", help="覆盖 Fixer 阶段任务描述")
    parser.add_argument("-m", "--model", default="", help="指定模型（例如 deepseek/deepseek-reasoner）")
    parser.add_argument("--timeout", type=int, default=900, help="每阶段超时时间（秒）")
    parser.add_argument("--base-image", default="", help="基础 Docker 镜像名（Issue + Docker 模式必填）")
    args = parser.parse_args()
    return run_pipeline(
        script_path=Path(__file__),
        target=args.issue_url,
        docker=args.docker,
        task_override=args.task,
        model_override=args.model,
        timeout_sec=args.timeout,
        allow_issue_delegate=True,
        base_image=args.base_image,
    )


if __name__ == "__main__":
    raise SystemExit(main())
