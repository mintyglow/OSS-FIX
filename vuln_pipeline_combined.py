#!/usr/bin/env python3
"""
消融实验：用单个 Agent 完成原 Stage1(Build) + Stage2(Exploit)，不跑 Fixer / Patch。
仅评估合并阶段是否同时达成「编译/构建」与「复现」目标；产物在 combine_outputs/。
不修改 vuln_pipeline_core.run_pipeline。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from vuln_pipeline_core import (
    ISSUE_RE,
    _docker_name_slug,
    _write_pipeline_result_json,
    bootstrap_shell,
    detect_target,
    ensure_clone,
    export_step_log_from_traj,
    infer_model,
    mini_stage_cmd,
    mini_stage_cmd_shell_quoted,
    run_stream,
)


def _finalize_combined_ablation_summary(report: Dict[str, Any]) -> None:
    """本消融不跑 Fixer/Patch：summary 只反映合并阶段内的 build / exploit 是否成功。"""
    build = report.get("build") or {}
    exploit = report.get("exploit") or {}
    compile_ok = build.get("success") is True
    repro_ok = exploit.get("success") is True
    report["summary"] = {
        "compile_success": compile_ok,
        "reproduce_success": repro_ok,
        "fixer_agent_success": None,
        "post_fix_autoverify_passed": None,
        "patch_agent_ran": False,
        "patch_agent_success": None,
        "fix_verified": None,
        "ablation_no_fixer_patch": True,
        "combined_build_exploit_success": compile_ok and repro_ok,
    }


def run_pipeline_combined_build_exploit(
    *,
    script_path: Path,
    target: str,
    docker: bool,
    task_override: str,
    model_override: str,
    timeout_sec: int,
    allow_issue_delegate: bool,
    base_image: str = "",
) -> int:
    root = script_path.resolve().parent
    cfg_dir = root / "config"
    be_cfg = cfg_dir / "build_exploit_agent.yaml"
    if not be_cfg.exists():
        print(f"ERROR: missing config: {be_cfg}")
        return 1

    run_id = os.environ.get("VULN_PIPELINE_RUN_ID", "").strip()
    if not run_id:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    env_prefix = os.environ.get("VULN_PIPELINE_RUN_PREFIX", "").strip()
    issue_slug = ""
    if not env_prefix:
        im = ISSUE_RE.match(target.strip())
        if im:
            owner, repo, num = im.groups()
            issue_slug = _docker_name_slug(f"{owner}-{repo}-{num}")
    run_label_prefix = env_prefix or issue_slug
    out_root = root / "combine_outputs"
    if run_label_prefix:
        run_out = out_root / f"run-{run_label_prefix}-{run_id}"
    else:
        run_out = out_root / f"run-{run_id}"
    run_out.mkdir(parents=True, exist_ok=True)

    target_type, repo_url = detect_target(target)
    issue_delegate = allow_issue_delegate and target_type == "issue" and repo_url is not None

    if docker and issue_delegate and not base_image.strip():
        print("ERROR: Issue + Docker 模式必须提供基础镜像名，请使用 --base-image <image:tag>")
        return 1

    if target_type == "repo_url" and repo_url:
        repo_path = ensure_clone(repo_url, run_out / "repo", run_out / "host-clone.log")
    elif issue_delegate:
        repo_path = root
    else:
        repo_path = Path(target).expanduser().resolve()
        if not repo_path.exists():
            print(f"ERROR: local path not found: {repo_path}")
            return 1

    model = model_override.strip() or infer_model()
    env = os.environ.copy()
    env.setdefault("MSWEA_CONFIGURED", "true")
    env.setdefault("MSWEA_SILENT_STARTUP", "1")
    if model:
        env["MSWEA_MODEL_NAME"] = model

    if issue_delegate:
        issue_artifacts = (
            "Put stage-local artifacts (extra sanitizer logs, handoff notes, evidence copies) under /new_project/out/ "
            "(created by bootstrap; keep trajectories in /out only for the orchestrator)."
        )
        if docker:
            delegated = "/src/<project>"
            path_note = (
                "Docker Issue: the host workspace is mounted at /project; pipeline writes trajectories to /out. "
                f"{issue_artifacts} "
                "Clone the target repository ONLY into /src/<project> (replace <project> with the repo directory name, e.g. wabt). "
                "Do NOT use /out/repo as source root (it is not mounted)."
            )
        else:
            delegated = "/tmp/mswea_issue_repo"
            path_note = (
                "Local Issue: clone into a dedicated path such as /tmp/mswea_issue_repo (or your repo-local tree). "
                f"{issue_artifacts.replace('/new_project/out/', 'new_project/out/')} "
                "Do not assume /out/repo exists."
            )
        stage1_task = (
            f"GitHub Issue context: {target}. Repository URL: {repo_url}. "
            f"Base runner image (provided by user): {base_image or 'N/A'}. "
            f"{path_note} "
            f"Then clone/setup source under {delegated} and create /new_project/build.sh. "
            "Audit real source only and produce exact ASan build/repro steps."
        )
        stage2_task = (
            f"Use stage1 handoff. Work on the real source tree at {delegated} (under /src/<repo> after clone). "
            f"{issue_artifacts} Reproduce with sanitizer logs."
        )
    else:
        stage1_task = "Audit real repository source only; provide concrete vulnerable locations and ASan build/repro steps."
        stage2_task = "Use stage1 handoff and reproduce vulnerability on real repository code paths with sanitizer logs."

    stage_be_task = (
        "ABLATION — single agent must complete BOTH build (compile/setup + build.sh) AND exploit (repro.sh + sanitizer).\n\n"
    )
    if task_override.strip():
        stage_be_task += f"--- Task / instance context ---\n{task_override.strip()}\n\n"
    stage_be_task += (
        f"--- Build-phase ---\n{stage1_task}\n\n"
        f"--- Exploit-phase ---\n{stage2_task}"
    )

    print(f"[+] Work dir    : {repo_path}")
    print(f"[+] Output dir  : {run_out}")
    print(f"[+] Mode        : {'docker' if docker else 'local'}")
    print("[+] Ablation    : combined Build+Exploit only (no Fixer/Patch; config/build_exploit_agent.yaml)")
    if docker:
        print(f"[+] Base image  : {base_image.strip() or os.getenv('VULN_PIPELINE_RUNNER_IMAGE', 'ubuntu:22.04')}")
    if model:
        print(f"[+] Model       : {model}")

    pipeline_report: Dict[str, Any] = {
        "schema_version": 1,
        "output_dir": str(run_out.resolve()),
        "work_dir": str(repo_path.resolve()),
        "docker": docker,
        "model": model,
        "ablation": {
            "mode": "combined_build_exploit_single_agent",
            "no_fixer_no_patch": True,
        },
    }

    if docker:
        runner_image = base_image.strip() or os.getenv("VULN_PIPELINE_RUNNER_IMAGE", "ubuntu:22.04").strip() or "ubuntu:22.04"
        skip_bootstrap = os.getenv("VULN_PIPELINE_SKIP_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")
        _inst_raw = env_prefix or issue_slug
        _inst = _docker_name_slug(_inst_raw) if _inst_raw else "pipeline"
        _ts = _docker_name_slug(run_id)
        docker_stage_base = f"feirubei-{_inst}-{_ts}"

        common_env: List[str] = []
        for k in [
            "MSWEA_CONFIGURED",
            "MSWEA_SILENT_STARTUP",
            "MSWEA_MODEL_NAME",
            "OPENAI_API_KEY",
            "OPENAI_API_BASE",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
        ]:
            common_env.extend(["-e", f"{k}={env.get(k, '')}"])
        common_env.extend(["-e", "MSWEA_GLOBAL_CONFIG_DIR=/out/.mswea"])

        def rm_container(name: str) -> None:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        def stage(
            stage_name: str,
            cfg_name: str,
            task: str,
            traj: str,
            log: str,
            steps: str,
            idx: int,
            image: str,
            do_bootstrap: bool,
        ) -> Tuple[int, str]:
            cname = f"{docker_stage_base}-s{idx}"
            cimage = f"{docker_stage_base}:stage{idx}"
            rm_container(cname)
            inner = bootstrap_shell(skip_bootstrap=not do_bootstrap) + "\ncd /project && " + mini_stage_cmd_shell_quoted(
                f"/agent-config/{cfg_name}", task, f"/out/{traj}", model, timeout_sec
            )
            cmd = [
                "docker",
                "run",
                "--name",
                cname,
                *common_env,
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                "-v",
                f"{repo_path}:/project",
                "-v",
                f"{run_out}:/out",
                "-v",
                f"{cfg_dir}:/agent-config:ro",
                image,
                "bash",
                "-lc",
                inner,
            ]
            print(f"[+] {stage_name} (base image: {image})")
            code = run_stream(cmd, log_path=run_out / log)
            export_step_log_from_traj(run_out / traj, run_out / steps)
            if code != 0:
                rm_container(cname)
                return code, image
            cc = run_stream(["docker", "commit", cname, cimage], log_path=run_out / f"docker-commit-stage{idx}.log")
            rm_container(cname)
            if cc != 0:
                return cc, image
            return 0, cimage

        chain = [f"base={runner_image}\n"]
        code, cur = stage(
            "Stage1+2 Build-Exploit-Agent (ablation)",
            "build_exploit_agent.yaml",
            stage_be_task,
            "stage1-combined.traj.json",
            "stage1-combined.log",
            "stage1-combined-steps.log",
            1,
            runner_image,
            not skip_bootstrap,
        )
        pipeline_report["build"] = {
            "stage": "compile_build",
            "agent": "Stage1+2 Build-Exploit-Agent",
            "success": code == 0,
            "exit_code": code,
            "ablation_merged_exploit": True,
        }
        pipeline_report["exploit"] = {
            "stage": "reproduce",
            "agent": "Stage1+2 Build-Exploit-Agent",
            "success": code == 0,
            "exit_code": code,
            "merged_with_build_stage": True,
        }
        chain.append(f"stage1_combined_be={cur}\n")
        (run_out / "docker-image-chain.txt").write_text("".join(chain), encoding="utf-8")

        pipeline_report["fixer"] = {
            "skipped": True,
            "omitted_in_ablation": True,
            "note": "Fixer not run in combined Build+Exploit ablation",
        }
        pipeline_report["post_fix_verify"] = {"ran": False, "passed": None, "skipped": True}
        pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None, "skipped": True}

        _finalize_combined_ablation_summary(pipeline_report)
        ok = bool(pipeline_report["summary"].get("combined_build_exploit_success"))
        pipeline_report["overall_pipeline_success"] = ok
        _write_pipeline_result_json(run_out, pipeline_report)

        if code != 0:
            return 1
        print("[+] Ablation: combined Build+Exploit finished; Fixer/Patch skipped by design")
    else:

        def stage_local(name: str, cfg: Path, task: str, traj: str, log: str, steps: str) -> int:
            print(f"[+] {name}")
            code = run_stream(
                mini_stage_cmd(str(cfg), task, str(run_out / traj), model, timeout_sec),
                cwd=repo_path,
                env=env,
                log_path=run_out / log,
            )
            export_step_log_from_traj(run_out / traj, run_out / steps)
            return code

        c_be = stage_local(
            "Stage1+2 Build-Exploit-Agent (ablation)",
            be_cfg,
            stage_be_task,
            "stage1-combined.traj.json",
            "stage1-combined.log",
            "stage1-combined-steps.log",
        )
        pipeline_report["build"] = {
            "stage": "compile_build",
            "agent": "Stage1+2 Build-Exploit-Agent",
            "success": c_be == 0,
            "exit_code": c_be,
            "ablation_merged_exploit": True,
        }
        pipeline_report["exploit"] = {
            "stage": "reproduce",
            "agent": "Stage1+2 Build-Exploit-Agent",
            "success": c_be == 0,
            "exit_code": c_be,
            "merged_with_build_stage": True,
        }
        pipeline_report["fixer"] = {
            "skipped": True,
            "omitted_in_ablation": True,
            "note": "Fixer not run in combined Build+Exploit ablation",
        }
        pipeline_report["post_fix_verify"] = {"ran": False, "passed": None, "skipped": True}
        pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None, "skipped": True}

        _finalize_combined_ablation_summary(pipeline_report)
        ok = bool(pipeline_report["summary"].get("combined_build_exploit_success"))
        pipeline_report["overall_pipeline_success"] = ok
        _write_pipeline_result_json(run_out, pipeline_report)

        if c_be != 0:
            return 1
        print("[+] Ablation: combined Build+Exploit finished; Fixer/Patch skipped by design")

    print("\n✅ Done (ablation: combined build+exploit, no Fixer/Patch)")
    print(f"Output: {run_out}")
    print("  - stage1-combined-*.log / .traj.json (merged Build+Exploit)")
    print("  - pipeline_result.json (see summary.combined_build_exploit_success)")
    if docker:
        print("  - docker-image-chain.txt, docker-commit-stage1.log")
    return 0


__all__ = ["run_pipeline_combined_build_exploit"]
