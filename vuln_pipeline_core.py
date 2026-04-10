#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ISSUE_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$")
REPO_RE = re.compile(r"^https?://github\.com/([^/]+?)/([^/]+?)(?:\.git)?/?$")


def _docker_name_slug(s: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "-", s.strip().lower())
    t = re.sub(r"-+", "-", t).strip("-")
    return t if t else "x"


def _write_pipeline_result_json(run_out: Path, data: Dict[str, Any]) -> None:
    (run_out / "pipeline_result.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _finalize_pipeline_summary(report: Dict[str, Any]) -> None:
    build = report.get("build") or {}
    exploit = report.get("exploit") or {}
    fixer = report.get("fixer") or {}
    pfv = report.get("post_fix_verify") or {}
    patch = report.get("patch") or {}

    compile_ok = build.get("success") is True
    repro_ok = exploit.get("success") is True
    fixer_ok = fixer.get("success") is True
    pfv_passed = pfv.get("passed")
    patch_ran = patch.get("ran") is True
    patch_ok = patch.get("success") is True

    fix_verified = (fixer_ok and pfv_passed is True) or (patch_ran and patch_ok)

    report["summary"] = {
        "compile_success": compile_ok,
        "reproduce_success": repro_ok,
        "fixer_agent_success": fixer_ok,
        "post_fix_autoverify_passed": pfv_passed if pfv.get("ran") else None,
        "patch_agent_ran": patch_ran,
        "patch_agent_success": patch_ok if patch_ran else None,
        "fix_verified": fix_verified,
    }


def run_stream(cmd: List[str], *, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None, log_path: Optional[Path] = None) -> int:
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fp = log_path.open("w", encoding="utf-8", errors="replace")
    else:
        fp = None
    try:
        p = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert p.stdout is not None
        for line in p.stdout:
            sys.stdout.write(line)
            if fp:
                fp.write(line)
        return p.wait()
    finally:
        if fp:
            fp.close()


def detect_target(target: str) -> Tuple[str, Optional[str]]:
    m = ISSUE_RE.match(target)
    if m:
        owner, repo, _ = m.groups()
        return "issue", f"https://github.com/{owner}/{repo}.git"
    m = REPO_RE.match(target)
    if m:
        owner, repo = m.groups()
        return "repo_url", f"https://github.com/{owner}/{repo}.git"
    return "local_path", None


def infer_model() -> Optional[str]:
    model = os.getenv("MSWEA_MODEL_NAME", "").strip()
    if model:
        return model
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        return "deepseek/deepseek-reasoner"
    if os.getenv("OPENAI_API_KEY", "").strip() and "deepseek" in os.getenv("OPENAI_API_BASE", "").lower():
        return "openai/deepseek-reasoner"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai/gpt-4o-mini"
    return None


def ensure_clone(repo_url: str, dest: Path, log_path: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if run_stream(["git", "clone", "--depth", "1", repo_url, str(dest)], log_path=log_path) != 0:
        raise RuntimeError(f"git clone failed: {repo_url}")
    return dest


def mini_stage_cmd(config_path: str, task: str, traj_path: str, model: Optional[str], timeout_sec: int) -> List[str]:
    cmd = ["timeout", f"{timeout_sec}s", "mini", "-y", "--exit-immediately", "-o", traj_path, "-c", config_path, "-t", task]
    if model:
        cmd.extend(["-m", model])
    return cmd


def mini_stage_cmd_shell_quoted(config_path: str, task: str, traj_path: str, model: Optional[str], timeout_sec: int) -> str:
    return " ".join(shlex.quote(x) for x in mini_stage_cmd(config_path, task, traj_path, model, timeout_sec))


def _apt_mirror_snippet() -> str:
    return (
        "if [ -f /etc/apt/sources.list ]; then\n"
        "  sed -i 's@http://.*archive.ubuntu.com/ubuntu@http://mirrors.aliyun.com/ubuntu@g' /etc/apt/sources.list || true\n"
        "  sed -i 's@http://.*security.ubuntu.com/ubuntu@http://mirrors.aliyun.com/ubuntu@g' /etc/apt/sources.list || true\n"
        "fi"
    )


def bootstrap_shell(skip_bootstrap: bool) -> str:
    if skip_bootstrap:
        return "set -e\nmkdir -p /out/.mswea /new_project/out\n"
    return (
        "set -euo pipefail\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "if ! command -v mini >/dev/null 2>&1; then\n"
        f"{_apt_mirror_snippet()}\n"
        "  apt-get update\n"
        "  apt-get install -y python3 python3-pip git build-essential curl ca-certificates docker.io\n"
        "  pip3 config set global.index-url https://mirrors.aliyun.com/pypi/simple/ || true\n"
        "  pip3 install --no-cache-dir mini-swe-agent\n"
        "fi\n"
        "mkdir -p /out/.mswea /new_project/out\n"
    )


def _collect_step_like_entries(node: object, out: List[Dict[str, object]]) -> None:
    if isinstance(node, dict):
        if set(node.keys()).intersection({"step", "action", "observation", "thought", "content", "tool"}):
            out.append(node)
        for v in node.values():
            _collect_step_like_entries(v, out)
    elif isinstance(node, list):
        for i in node:
            _collect_step_like_entries(i, out)


def export_step_log_from_traj(traj_path: Path, out_path: Path) -> None:
    if not traj_path.exists():
        out_path.write_text(f"[warn] trajectory not found: {traj_path}\n", encoding="utf-8")
        return
    try:
        raw = traj_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        entries: List[Dict[str, object]] = []
        _collect_step_like_entries(data, entries)
        lines: List[str] = [f"# Step log extracted from: {traj_path.name}", ""]
        if not entries:
            lines.append(raw[:20000])
        else:
            for idx, item in enumerate(entries, start=1):
                lines.append(f"===== STEP {idx} =====")
                for k in ["step", "thought", "action", "tool", "observation", "content"]:
                    if k in item:
                        v = item[k]
                        lines.append(f"[{k}]")
                        lines.append(json.dumps(v, ensure_ascii=False, indent=2) if isinstance(v, (dict, list)) else str(v))
                        lines.append("")
        out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except Exception as e:
        out_path.write_text(f"[warn] failed to parse trajectory: {e}\n", encoding="utf-8")


def resolve_paths(script_path: Path) -> Tuple[Path, Path, Path, Path, Path]:
    root = script_path.resolve().parent
    cfg = root / "config"
    return (
        root,
        cfg / "build_agent.yaml",
        cfg / "exploiter_agent.yaml",
        cfg / "fixer_agent.yaml",
        cfg / "patch_agent.yaml",
    )


def _post_fix_verify_bash_inner() -> str:
    return r"""set -euo pipefail
LOG=/out/post-fix-verify.log
rm -f "$LOG"
mkdir -p /new_project/out
if [ ! -f /new_project/build.sh ] || [ ! -f /new_project/repro.sh ]; then
  echo "post-fix-verify: no build.sh/repro.sh, skip automated check (trust Fixer exit 0)" >>"$LOG"
  exit 0
fi
WORK=""
if [ -d /src ]; then
  D=$(find /src -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
  [ -n "$D" ] && WORK="$D"
fi
[ -z "$WORK" ] && [ -d /project ] && WORK="/project"
if [ -z "$WORK" ]; then
  echo "post-fix-verify: could not locate workdir, skip (trust Fixer exit 0)" >>"$LOG"
  exit 0
fi
cd "$WORK"
set +e
bash /new_project/build.sh >>"$LOG" 2>&1
bash /new_project/repro.sh >>"$LOG" 2>&1
set -e
PAT="(ERROR:.*Sanitizer|^==[0-9]+==ERROR:|SUMMARY:.*Sanitizer)"
FOUND=0
if [ -s /new_project/repro_sanitizer.log ]; then
  grep -qE "$PAT" /new_project/repro_sanitizer.log 2>/dev/null && FOUND=1
else
  grep -vE '^\+\s' "$LOG" 2>/dev/null | grep -qE "$PAT" 2>/dev/null && FOUND=1
fi
if [ "$FOUND" -eq 1 ]; then
  echo "post-fix-verify: sanitizer still present -> need Patch-Agent" >>"$LOG"
  exit 1
fi
echo "post-fix-verify: OK (no sanitizer error pattern in log)" >>"$LOG"
exit 0
"""


def post_fix_verify_docker(
    image: str,
    repo_path: Path,
    run_out: Path,
    common_env: List[str],
) -> bool:
    inner = bootstrap_shell(True) + "\n" + _post_fix_verify_bash_inner()
    code = run_stream(
        [
            "docker",
            "run",
            "--rm",
            *common_env,
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            f"{repo_path}:/project",
            "-v",
            f"{run_out}:/out",
            image,
            "bash",
            "-lc",
            inner,
        ],
        log_path=run_out / "post-fix-verify-docker.log",
    )
    return code == 0


_SANITIZER_GREP = re.compile(
    r"(ERROR:.*Sanitizer|^==[0-9]+==ERROR:|SUMMARY:.*Sanitizer)",
    re.MULTILINE,
)


def _strip_shell_xtrace_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not re.match(r"^\+\s", line))


def post_fix_verify_local(repo_path: Path, run_out: Path) -> bool:
    log_path = run_out / "post-fix-verify.log"
    np = repo_path / "new_project"
    if not (np / "build.sh").exists() or not (np / "repro.sh").exists():
        log_path.write_text(
            "post-fix-verify: no new_project/build.sh or repro.sh, skip (trust Fixer exit 0)\n",
            encoding="utf-8",
        )
        return True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        np.mkdir(parents=True, exist_ok=True)
        (np / "out").mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as fp:
            for script in ("build.sh", "repro.sh"):
                p = subprocess.run(
                    ["bash", str(np / script)],
                    cwd=str(repo_path),
                    stdout=fp,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                )
                fp.write(f"\n# exit {script}: {p.returncode}\n")
    except FileNotFoundError:
        log_path.write_text("post-fix-verify: bash not found, skip (trust Fixer exit 0)\n", encoding="utf-8")
        return True
    rs_log = np / "repro_sanitizer.log"
    if rs_log.is_file() and rs_log.stat().st_size > 0:
        text = rs_log.read_text(encoding="utf-8", errors="replace")
    else:
        text = _strip_shell_xtrace_lines(log_path.read_text(encoding="utf-8", errors="replace"))
    if _SANITIZER_GREP.search(text):
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write("\npost-fix-verify: sanitizer still present -> need Patch-Agent\n")
        return False
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write("\npost-fix-verify: OK (no sanitizer error pattern in log)\n")
    return True


def run_pipeline(
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
    root, build_cfg, exploit_cfg, fix_cfg, patch_cfg = resolve_paths(script_path)
    cfg_dir = build_cfg.parent
    for p in [build_cfg, exploit_cfg, fix_cfg, patch_cfg]:
        if not p.exists():
            print(f"ERROR: missing config: {p}")
            return 1

    # SecBench 等入口可设置 VULN_PIPELINE_RUN_PREFIX（如 instance_id）与 VULN_PIPELINE_RUN_ID（时间戳）。
    # GitHub Issue URL（run_vuln_issue.py）未设 prefix 时，自动用 owner-repo-{num}，生成 run-{slug}-{timestamp}。
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
    if run_label_prefix:
        run_out = root / "outputs" / f"run-{run_label_prefix}-{run_id}"
    else:
        run_out = root / "outputs" / f"run-{run_id}"
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

    fixer_task = task_override.strip() or "Fix vulnerabilities with minimal source-only changes. Use prior stage evidence, rebuild, and verify no longer reproducible."
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
        stage3_task = (
            f"{fixer_task} Edit only under the cloned repository path corresponding to {delegated}. "
            f"{issue_artifacts} Verify and commit fixed container image."
        )
        stage4_task = (
            f"Fixer-Agent did not achieve verified repair (failed run or repro still shows sanitizer). {fixer_task} "
            f"Work on the same source tree as {delegated}. {issue_artifacts} "
            "Use a different minimal strategy than the prior fix attempt."
        )
    else:
        stage1_task = "Audit real repository source only; provide concrete vulnerable locations and ASan build/repro steps."
        stage2_task = "Use stage1 handoff and reproduce vulnerability on real repository code paths with sanitizer logs."
        stage3_task = f"{fixer_task} Patch repository source only and verify."
        stage4_task = (
            f"Fixer-Agent did not achieve verified repair (failed run or repro still shows sanitizer). {fixer_task} "
            "Use a different minimal strategy than the prior fix attempt."
        )

    print(f"[+] Work dir    : {repo_path}")
    print(f"[+] Output dir  : {run_out}")
    print(f"[+] Mode        : {'docker' if docker else 'local'}")
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
    }

    if docker:
        runner_image = base_image.strip() or os.getenv("VULN_PIPELINE_RUNNER_IMAGE", "ubuntu:22.04").strip() or "ubuntu:22.04"
        skip_bootstrap = os.getenv("VULN_PIPELINE_SKIP_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")
        # 容器 / 镜像：feirubei-{label}-{timestamp}（与 outputs 目录一致：env prefix 或 GitHub Issue 推导的 issue_slug）
        _inst_raw = env_prefix or issue_slug
        _inst = _docker_name_slug(_inst_raw) if _inst_raw else "pipeline"
        _ts = _docker_name_slug(run_id)
        docker_stage_base = f"feirubei-{_inst}-{_ts}"

        common_env: List[str] = []
        for k in ["MSWEA_CONFIGURED", "MSWEA_SILENT_STARTUP", "MSWEA_MODEL_NAME", "OPENAI_API_KEY", "OPENAI_API_BASE", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"]:
            common_env.extend(["-e", f"{k}={env.get(k, '')}"])
        common_env.extend(["-e", "MSWEA_GLOBAL_CONFIG_DIR=/out/.mswea"])

        def rm_container(name: str) -> None:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        def stage(stage_name: str, cfg_name: str, task: str, traj: str, log: str, steps: str, idx: int, image: str, do_bootstrap: bool) -> Tuple[int, str]:
            cname = f"{docker_stage_base}-s{idx}"
            cimage = f"{docker_stage_base}:stage{idx}"
            rm_container(cname)
            inner = bootstrap_shell(skip_bootstrap=not do_bootstrap) + "\ncd /project && " + mini_stage_cmd_shell_quoted(
                f"/agent-config/{cfg_name}", task, f"/out/{traj}", model, timeout_sec
            )
            cmd = [
                "docker", "run", "--name", cname, *common_env,
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{repo_path}:/project",
                "-v", f"{run_out}:/out",
                "-v", f"{cfg_dir}:/agent-config:ro",
                image, "bash", "-lc", inner,
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
        code, cur = stage("Stage1 Build-Agent", "build_agent.yaml", stage1_task, "stage1.traj.json", "stage1-build.log", "stage1-steps.log", 1, runner_image, not skip_bootstrap)
        pipeline_report["build"] = {
            "stage": "compile_build",
            "agent": "Stage1 Build-Agent",
            "success": code == 0,
            "exit_code": code,
        }
        chain.append(f"stage1={cur}\n")
        if code != 0:
            pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
            pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
            pipeline_report["fixer"] = {"stage": "fix", "agent": "Stage3 Fixer-Agent", "success": False, "exit_code": -1, "skipped": True}
            pipeline_report["exploit"] = {"stage": "reproduce", "agent": "Stage2 Exploiter-Agent", "success": False, "exit_code": -1, "skipped": True}
            _finalize_pipeline_summary(pipeline_report)
            pipeline_report["overall_pipeline_success"] = False
            _write_pipeline_result_json(run_out, pipeline_report)
            return 1
        code, cur = stage("Stage2 Exploiter-Agent", "exploiter_agent.yaml", stage2_task, "stage2.traj.json", "stage2-exploit.log", "stage2-steps.log", 2, cur, False)
        pipeline_report["exploit"] = {
            "stage": "reproduce",
            "agent": "Stage2 Exploiter-Agent",
            "success": code == 0,
            "exit_code": code,
        }
        chain.append(f"stage2={cur}\n")
        if code != 0:
            pipeline_report["fixer"] = {"stage": "fix", "agent": "Stage3 Fixer-Agent", "success": False, "exit_code": -1, "skipped": True}
            pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
            pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
            _finalize_pipeline_summary(pipeline_report)
            pipeline_report["overall_pipeline_success"] = False
            _write_pipeline_result_json(run_out, pipeline_report)
            return 1
        code, cur = stage("Stage3 Fixer-Agent", "fixer_agent.yaml", stage3_task, "stage3.traj.json", "stage3-fix.log", "stage3-steps.log", 3, cur, False)
        pipeline_report["fixer"] = {
            "stage": "fix",
            "agent": "Stage3 Fixer-Agent",
            "success": code == 0,
            "exit_code": code,
        }
        if code == 0:
            chain.append(f"stage3={cur}\n")
        else:
            chain.append("# stage3 Fixer-Agent failed (no new image; still on stage2 image)\n")
        pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
        if code != 0:
            need_patch = True
        else:
            pfv_ok = post_fix_verify_docker(cur, repo_path, run_out, common_env)
            pipeline_report["post_fix_verify"] = {"ran": True, "passed": pfv_ok}
            need_patch = not pfv_ok
        pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
        if need_patch:
            print("[+] Fixer did not verify (or failed); running Stage4 Patch-Agent")
            code, cur = stage(
                "Stage4 Patch-Agent",
                "patch_agent.yaml",
                stage4_task,
                "stage4.traj.json",
                "stage4-patch.log",
                "stage4-steps.log",
                4,
                cur,
                False,
            )
            pipeline_report["patch"] = {
                "ran": True,
                "success": code == 0,
                "exit_code": code,
                "agent": "Stage4 Patch-Agent",
            }
            if code == 0:
                chain.append(f"stage4={cur}\n")
            else:
                chain.append("# stage4 Patch-Agent failed (no new image)\n")
                _finalize_pipeline_summary(pipeline_report)
                pipeline_report["overall_pipeline_success"] = False
                _write_pipeline_result_json(run_out, pipeline_report)
                return 1
        else:
            print("[+] Fixer verified (build+repro, no sanitizer pattern); skipping Patch-Agent")
        (run_out / "docker-image-chain.txt").write_text("".join(chain), encoding="utf-8")

        patch_shell = (
            bootstrap_shell(True)
            + "\n"
            + r"""set -e
mkdir -p /new_project/out
FIXED=0
if [ -d /src ]; then
  for d in /src/*/; do
    [ -d "${d}.git" ] || continue
    (cd "$d" && git diff > /out/fix.patch) && FIXED=1 && break
  done
fi
if [ "$FIXED" != 1 ]; then
  cd /project && (git rev-parse >/dev/null 2>&1 && git diff > /out/fix.patch || true)
fi
"""
        )
        run_stream(
            [
                "docker", "run", "--rm",
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{repo_path}:/project",
                "-v", f"{run_out}:/out",
                cur, "bash", "-lc", patch_shell,
            ],
            log_path=run_out / "patch-export.log",
        )
        _finalize_pipeline_summary(pipeline_report)
        pipeline_report["overall_pipeline_success"] = True
        _write_pipeline_result_json(run_out, pipeline_report)
    else:
        def stage_local(name: str, cfg: Path, task: str, traj: str, log: str, steps: str) -> int:
            print(f"[+] {name}")
            code = run_stream(mini_stage_cmd(str(cfg), task, str(run_out / traj), model, timeout_sec), cwd=repo_path, env=env, log_path=run_out / log)
            export_step_log_from_traj(run_out / traj, run_out / steps)
            return code

        c1 = stage_local("Stage1 Build-Agent", build_cfg, stage1_task, "stage1.traj.json", "stage1-build.log", "stage1-steps.log")
        pipeline_report["build"] = {
            "stage": "compile_build",
            "agent": "Stage1 Build-Agent",
            "success": c1 == 0,
            "exit_code": c1,
        }
        if c1 != 0:
            pipeline_report["exploit"] = {"stage": "reproduce", "agent": "Stage2 Exploiter-Agent", "success": False, "exit_code": -1, "skipped": True}
            pipeline_report["fixer"] = {"stage": "fix", "agent": "Stage3 Fixer-Agent", "success": False, "exit_code": -1, "skipped": True}
            pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
            pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
            _finalize_pipeline_summary(pipeline_report)
            pipeline_report["overall_pipeline_success"] = False
            _write_pipeline_result_json(run_out, pipeline_report)
            return 1

        c2 = stage_local("Stage2 Exploiter-Agent", exploit_cfg, stage2_task, "stage2.traj.json", "stage2-exploit.log", "stage2-steps.log")
        pipeline_report["exploit"] = {
            "stage": "reproduce",
            "agent": "Stage2 Exploiter-Agent",
            "success": c2 == 0,
            "exit_code": c2,
        }
        if c2 != 0:
            pipeline_report["fixer"] = {"stage": "fix", "agent": "Stage3 Fixer-Agent", "success": False, "exit_code": -1, "skipped": True}
            pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
            pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
            _finalize_pipeline_summary(pipeline_report)
            pipeline_report["overall_pipeline_success"] = False
            _write_pipeline_result_json(run_out, pipeline_report)
            return 1

        c3 = stage_local("Stage3 Fixer-Agent", fix_cfg, stage3_task, "stage3.traj.json", "stage3-fix.log", "stage3-steps.log")
        pipeline_report["fixer"] = {
            "stage": "fix",
            "agent": "Stage3 Fixer-Agent",
            "success": c3 == 0,
            "exit_code": c3,
        }
        pipeline_report["post_fix_verify"] = {"ran": False, "passed": None}
        if c3 != 0:
            need_patch = True
        else:
            pfv_ok = post_fix_verify_local(repo_path, run_out)
            pipeline_report["post_fix_verify"] = {"ran": True, "passed": pfv_ok}
            need_patch = not pfv_ok

        pipeline_report["patch"] = {"ran": False, "success": None, "exit_code": None}
        if need_patch:
            print("[+] Fixer did not verify (or failed); running Stage4 Patch-Agent")
            c4 = stage_local(
                "Stage4 Patch-Agent",
                patch_cfg,
                stage4_task,
                "stage4.traj.json",
                "stage4-patch.log",
                "stage4-steps.log",
            )
            pipeline_report["patch"] = {
                "ran": True,
                "success": c4 == 0,
                "exit_code": c4,
                "agent": "Stage4 Patch-Agent",
            }
            if c4 != 0:
                _finalize_pipeline_summary(pipeline_report)
                pipeline_report["overall_pipeline_success"] = False
                _write_pipeline_result_json(run_out, pipeline_report)
                return 1
        else:
            print("[+] Fixer verified (build+repro, no sanitizer pattern); skipping Patch-Agent")

        _finalize_pipeline_summary(pipeline_report)
        pipeline_report["overall_pipeline_success"] = True
        _write_pipeline_result_json(run_out, pipeline_report)

    print("\n✅ Done")
    print(f"Output: {run_out}")
    print("  - stage1/2/3-*.log (+ stage4-* if Patch-Agent ran)")
    print("  - stage1/2/3.traj.json (+ stage4 if Patch-Agent ran)")
    print("  - stage1/2/3-steps.log (+ stage4 if Patch-Agent ran)")
    print("  - post-fix-verify*.log (automated check after Fixer)")
    print("  - pipeline_result.json (compile / reproduce / fix verification summary)")
    print("  - (in container) stage artifacts under /new_project/out/")
    if docker:
        print("  - docker-image-chain.txt")
        print("  - docker-commit-stage{1,2,3}.log (+ stage4 if Patch-Agent ran)")
    print("  - fix.patch (if repository has diff)")
    return 0
