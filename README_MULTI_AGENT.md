# Multi-Agent 漏洞修复流水线（完整说明）

本项目在 **mini-swe-agent** 上实现多阶段编排：**构建审计 → 复现（Sanitizer）→ 修复 →（可选）Patch-Agent**，核心逻辑在 `vuln_pipeline_core.py`。  
快速上手见 [`QUICK_START.md`](QUICK_START.md)，目录与模块见 [`OSS-FIX_structure.md`](OSS-FIX_structure.md)。

---

## 1. 入口脚本一览

| 脚本 | 作用 | 典型场景 |
|------|------|----------|
| [`run_vuln_local.py`](run_vuln_local.py) | 宿主机执行三阶段，`docker=False` | 本地已有仓库或希望先在宿主机 clone GitHub 仓库再跑 |
| [`run_vuln_docker.py`](run_vuln_docker.py) | 容器内执行三阶段，`docker=True` | 需要隔离环境、与生产一致的 Linux 工具链 |
| [`run_vuln_issue.py`](run_vuln_issue.py) | Issue 委托模式：`allow_issue_delegate=True`，**不**在外层提前 clone，由 Build-Agent 准备仓库 | 漏洞信息主要来自 GitHub Issue |
| [`run_secbench_local.py`](run_secbench_local.py) | 读取 `secbench_details.json`，按 `instance_id` 使用评测镜像 `hwiwonlee/secb.eval.x86_64.<instance_id>:patch` | SecBench / 基准单条评测 |
| [`run_vuln_pipeline.py`](run_vuln_pipeline.py) | 兼容旧命令的**统一入口**：自动识别本地路径、仓库 URL、Issue URL | 历史脚本或一条命令覆盖多种 target |

**说明：** `run_vuln_local.py` / `run_vuln_docker.py` 的 `target` 均为**本地路径或 GitHub 仓库 HTTPS URL**（脚本侧会 clone）；**只有** `run_vuln_issue.py` 使用 Issue URL 并走委托逻辑。

---

## 2. 阶段与 Agent 配置

### 2.1 流水线阶段

| 阶段 | 配置（相对仓库根） | 职责概要 |
|------|---------------------|----------|
| Stage 1 | `config/build_agent.yaml` | 审计与构建策略、`build.sh` 等 |
| Stage 2 | `config/exploiter_agent.yaml` | 复现漏洞、Sanitizer 证据 |
| Stage 3 | `config/fixer_agent.yaml` | 最小化修复与验证 |
| Stage 4（可选） | `config/patch_agent.yaml` | Fixer 未通过自动校验或仍检测到 Sanitizer 模式时，作为备选修复 |

Stage 4 是否运行由流水线内的 **post-fix 自动校验** 与 Fixer 结果共同决定（见下文 `pipeline_result.json`）。

### 2.2 各 Agent 职责（简要）

| 阶段 | 作用 |
|------|------|
| Build-Agent | 理解工程结构、给出可重复的 ASan 构建与复现步骤 |
| Exploiter-Agent | 按 Stage1 手复现并收集 Sanitizer 日志 |
| Fixer-Agent | 在真实源码上做最小修复并尝试验证 |
| Patch-Agent（可选） | Fixer 路径未达成“已验证修复”时的补充策略 |

---

## 3. 安装与模型配置

```bash
cd mini-swe-agent
pip install -e .
cd ..
```

环境变量（至少配置一种可用的 LLM）：

- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`
- DeepSeek：`DEEPSEEK_API_KEY`；未设置 `MSWEA_MODEL_NAME` 时，`infer_model()` 在检测到 DeepSeek Key 时会默认使用 `deepseek/deepseek-reasoner`

**Docker 模式：** 容器内运行 `mini` 时若无配置可能卡在首次向导。流水线会通过环境变量默认设置 `MSWEA_CONFIGURED=true` 等，并传入常见 API Key；若本机已用 `mini-extra config` 配置，宿主 `~/.config/mini-swe-agent` 在存在时会被只读挂载（行为以当前 `vuln_pipeline_core` 实现为准）。

---

## 4. 用法示例

### 4.1 本地模式（宿主机）

```bash
python3 run_vuln_local.py /path/to/project
python3 run_vuln_local.py /path/to/project -t "Fix memory safety issues with minimal changes" -m deepseek/deepseek-reasoner --timeout 900
```

也支持传入 **GitHub 仓库 URL**（先在宿主机 clone 再跑）：

```bash
python3 run_vuln_local.py https://github.com/owner/repo
```

### 4.2 Docker 模式（容器链）

```bash
python3 run_vuln_docker.py /path/to/project
python3 run_vuln_docker.py https://github.com/owner/repo --timeout 900
# 可选：指定基础镜像（否则默认 ubuntu:22.04 或环境变量 VULN_PIPELINE_RUNNER_IMAGE）
python3 run_vuln_docker.py /path/to/project --base-image ubuntu:22.04
```

### 4.3 GitHub Issue 模式

Issue URL 交给 Build-Agent，由其负责 clone 与环境准备：

```bash
# 宿主机执行三阶段（不强制 Docker）
python3 run_vuln_issue.py "https://github.com/owner/repo/issues/123" -t "Fix vulnerability described in this issue"

# Issue + Docker：必须提供 --base-image（评测镜像或自建镜像）
python3 run_vuln_issue.py "https://github.com/owner/repo/issues/123" --docker \
  --base-image ubuntu:22.04 \
  -t "Fix vulnerability described in this issue"
```

### 4.4 兼容入口 `run_vuln_pipeline.py`

```bash
python3 run_vuln_pipeline.py /path/or/repo-url [--docker] [--base-image ...] [-t ...] [-m ...] [--timeout 900]
```

当 `target` 为 Issue URL 时，会自动启用 Issue 委托逻辑（与 `run_vuln_issue.py` 一致）。

### 4.5 SecBench 单实例

需要 `secbench_details.json`（或 `--json` 指定），详见 [`run.md`](run.md)。

```bash
python3 run_secbench_local.py --instance_id mruby.cve-2022-1201 \
  --json secbench_details.json \
  --model deepseek/deepseek-reasoner \
  --timeout 1800
```

镜像默认：`hwiwonlee/secb.eval.x86_64.<instance_id>:patch`，也可用 `--image` 覆盖。

---

## 5. 输出目录与产物命名

每次运行会在项目根下 `outputs/` 生成目录：

| 情形 | 目录名模式 |
|------|------------|
| 普通运行（无额外前缀） | `outputs/run-<时间戳>/` |
| GitHub Issue URL 且未设 `VULN_PIPELINE_RUN_PREFIX` | `outputs/run-<owner-repo-编号>-<时间戳>/`（由 Issue URL 推导 slug） |
| SecBench（`run_secbench_local.py`） | `outputs/run-<instance_id>-<时间戳>/` |

**主要文件：**

- `stage1-build.log`、`stage2-exploit.log`、`stage3-fix.log`（若跑了 Patch-Agent 还有 `stage4-*`）
- `stage1/2/3.traj.json`、`*-steps.log`（及可选 `stage4`）
- `post-fix-verify*.log`：修复后的自动构建/复现与 Sanitizer 模式检查
- **`pipeline_result.json`**：整次运行的结构化汇总（见下节）
- `fix.patch`：若仓库侧能生成 diff
- Docker 模式额外：`docker-image-chain.txt`、`docker-commit-stage*.log`

---

## 6. `pipeline_result.json` 摘要字段

写入前会调用 `_finalize_pipeline_summary`，`summary` 中常见字段包括：

- `compile_success`：Stage1 是否成功  
- `reproduce_success`：Stage2 是否成功  
- `fixer_agent_success`：Stage3 是否成功  
- `post_fix_autoverify_passed`：自动校验是否通过（未运行则为 `null`）  
- `patch_agent_ran` / `patch_agent_success`：是否运行 Stage4 及是否成功  
- **`fix_verified`**：综合判定（Fixer + 自动校验 或 Patch-Agent 成功路径）

详细结构以当次运行生成的 JSON 为准。

---

## 7. 命令行参数（统一约定）

各入口均支持（默认值以脚本 `--help` 为准）：

| 参数 | 说明 |
|------|------|
| `target` / `issue_url` | 目标路径、仓库 URL 或 Issue URL |
| `-t` / `--task` | 覆盖 Fixer（及关联阶段）任务描述 |
| `-m` / `--model` | 覆盖 `MSWEA_MODEL_NAME` |
| `--timeout` | 每阶段超时（秒）；`run_vuln_*.py` 默认 **900**，`run_secbench_local.py` 默认 **1800** |
| `--docker` | 仅 `run_vuln_issue.py` / `run_vuln_pipeline.py`：是否容器执行 |
| `--base-image` | Docker 时使用的基础镜像；**Issue + Docker 时必填**。`run_vuln_local.py` 接受该参数但**忽略**（为统一 CLI 保留） |

`run_secbench_local.py` 另有：`--instance_id`（必填）、`--json`、`--image`、`--model`、`--timeout`。

---

## 8. 常见任务描述模板

- `Fix buffer overflow in strcpy and keep behavior unchanged`
- `Fix format string vulnerability and verify with ASan`
- `Fix all memory safety vulnerabilities with minimal changes`
- `Fix vulnerability described in this GitHub issue: <issue_url>`

---

## 9. 常见问题

- **找不到 `mini` 命令**  
  在 `mini-swe-agent` 目录执行 `pip install -e .`。

- **Docker 模式失败**  
  检查 `docker version`、镜像拉取与网络；Issue + Docker 是否已传 `--base-image`。

- **Docker 构建时 `apt` 502 / 官方源不稳定**  
  生成的 `Dockerfile` 可能已换国内镜像；若仍失败可稍后重试或按本次 `outputs/run-*/Dockerfile.vuln` 自行调整。

- **没有 `fix.patch`**  
  目标可能不是 git 仓库，或本次运行未产生可导出的 diff。

- **模型调用失败**  
  检查对应 API Key 与 `MSWEA_MODEL_NAME`。

---

## 10. 相关文档

| 文档 | 内容 |
|------|------|
| [`QUICK_START.md`](QUICK_START.md) | 最短路径上手 |
| [`OSS-FIX_structure.md`](OSS-FIX_structure.md) | 目录结构、配置路径、与核心模块对照 |
| [`README.md`](README.md) | 项目总览 |
