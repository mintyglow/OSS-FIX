import json
import argparse
import sys
import os
from pathlib import Path
from datetime import datetime

# 1. 确保路径解析正确，能够导入同目录下的 vuln_pipeline_core
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vuln_pipeline_core import run_pipeline
from vuln_pipeline_combined import run_pipeline_combined_build_exploit

def load_secbench_info(instance_id, json_path):
    """从 JSON 数据库中加载漏洞实例详情"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for entry in data:
            if entry['instance_id'] == instance_id:
                return entry
    except Exception as e:
        print(f"[-] 读取 JSON 失败: {e}")
    return None

def main():
    parser = argparse.ArgumentParser(description="OSS-Fix: SecBench 自动化修复工具")
    parser.add_argument("--instance_id", required=True, help="漏洞实例ID (如: mruby.cve-2022-1201)")
    # image 变为可选参数
    parser.add_argument("--image", help="手动指定 Docker 镜像 (若不指定则根据 ID 自动推断)")
    parser.add_argument("--json", default="secbench_details.json", help="SecBench 数据文件路径")
    parser.add_argument("--model", default="deepseek/deepseek-reasoner", help="使用的 LLM 模型")
    parser.add_argument("--timeout", type=int, default=1800, help="任务超时时间(秒)")
    parser.add_argument(
        "--ablation-combined-be",
        action="store_true",
        help="消融：单 Agent 合并编译+复现（build_exploit_agent.yaml），不跑 Fixer/Patch；结果在 combine_outputs/；默认仍为两阶段 Build+Exploit",
    )
    args = parser.parse_args()

    # 1. 加载元数据
    info = load_secbench_info(args.instance_id, args.json)
    if not info:
        print(f"❌ Error: 在 {args.json} 中未找到实例 {args.instance_id}")
        return 1

    # 2. 自动推断镜像逻辑
    # 规律：hwiwonlee/secb.eval.x86_64.[instance_id]:patch
    target_image = args.image if args.image else f"hwiwonlee/secb.eval.x86_64.{args.instance_id}:patch"

    # 3. 输出目录：默认 outputs/；消融合并模式与 run_pipeline_combined_build_exploit 一致用 combine_outputs/
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.environ["VULN_PIPELINE_RUN_PREFIX"] = args.instance_id
    os.environ["VULN_PIPELINE_RUN_ID"] = timestamp
    _out_base = _REPO_ROOT / ("combine_outputs" if args.ablation_combined_be else "outputs")
    run_out = _out_base / f"run-{args.instance_id}-{timestamp}"
    run_out.mkdir(parents=True, exist_ok=True)

    # 4. 创建影子目录绕过本地代码检查
    fake_repo_path = run_out / "repo"
    fake_repo_path.mkdir(parents=True, exist_ok=True)
    (fake_repo_path / ".skipped_clone").touch()  # 配合 vuln_pipeline_core 的修改

    # 5. 生成针对性任务指令
    task_desc = (
        f"Target Project: {info['project_name']}\n"
        f"Vulnerability: {args.instance_id}\n"
        f"Description: {info['bug_description']}\n\n"
        f"CRITICAL INSTRUCTION:\n"
        f"1. 源代码已存在于容器内: {info['work_dir']}\n"
        f"2. 不要使用本地挂载目录（它是空的）。\n"
        f"3. 任何编译或测试前必须先执行 'cd {info['work_dir']}'。\n"
        f"4. 镜像内已预装 ASan 环境和复现脚本。"
    )

    print(f"🚀 启动修复流水线: {args.instance_id}")
    if args.ablation_combined_be:
        print("📌 消融模式: Build+Exploit 合并为单 Agent (config/build_exploit_agent.yaml)")
    print(f"📦 使用镜像: {target_image}")
    print(f"🤖 使用模型: {args.model}")
    print(f"📁 输出目录: {run_out}")

    # 6. 调用核心流水线（环境变量已设，与上面 run_out 为同一路径）
    try:
        runner = run_pipeline_combined_build_exploit if args.ablation_combined_be else run_pipeline
        return runner(
            script_path=_REPO_ROOT / "run_vuln_issue.py",
            target=str(fake_repo_path),
            docker=True,
            task_override=task_desc,
            model_override=args.model,
            timeout_sec=args.timeout,
            allow_issue_delegate=False,
            base_image=target_image,
        )
    except Exception as e:
        print(f"💥 运行崩溃: {e}")
        return 1
    finally:
        for _k in ("VULN_PIPELINE_RUN_PREFIX", "VULN_PIPELINE_RUN_ID"):
            os.environ.pop(_k, None)

if __name__ == "__main__":
    sys.exit(main())