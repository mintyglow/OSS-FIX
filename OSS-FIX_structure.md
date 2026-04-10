# 项目结构说明

本文描述 **feirubei** 仓库的目录与模块职责，便于阅读代码与二次开发。  
快速上手见 `QUICK_START.md`，多 Agent 能力与入口总览见 `README_MULTI_AGENT.md`，SecBench 服务器评测见 `README_SECBENCH_EVAL.md`。

---

## 1. 仓库根目录（概念树）

```
feirubei/
├── vuln_pipeline_core.py      # 多阶段流水线核心（Docker / 本地、Issue、产物路径、pipeline_result.json）
├── run_vuln_pipeline.py       # 统一 CLI：本地路径 / GitHub 仓库 URL / Issue URL
├── run_vuln_local.py          # 本地仓库，宿主机执行 mini
├── run_vuln_docker.py         # 本地仓库，Docker 内执行 mini
├── run_vuln_issue.py          # GitHub Issue URL，Delegate 模式（需 --docker 时配合 --base-image）
├── run_secbench_local.py      # SecBench：instance_id + 评测镜像 hwiwonlee/secb.eval.x86_64.*:patch
├── run_multi_agent_orchestrator.py   # 批量/编排（若项目内仍在使用）
├── run.sh                     # 示例：批量调用 run_secbench_local（可选）
├── secbench_details.json      # SecBench 实例元数据（体积大时可只带子集）
├── keys.cfg.example                  # 密钥模板（复制为 keys.cfg；勿提交真实 keys）
├── config/                    # 流水线 Agent YAML（Build / Exploit / Fixer / Patch / vulnerability_fix）
├── outputs/                   # 运行产物（默认 gitignore 或勿提交）
├── mini-swe-agent/            # 上游 mini-swe-agent（默认 mini.yaml 等仍在此目录）

```

---

## 2. 核心模块：`vuln_pipeline_core.py`

| 职责 | 说明 |
|------|------|
| **阶段编排** | Stage1 Build → Stage2 Exploit → Stage3 Fixer；可选 Stage4 Patch-Agent（Fixer 未通过自动校验或失败时） |
| **运行模式** | `docker=True`：每阶段 `docker run` + `docker commit` 链式镜像；`docker=False`：本机 `mini` |
| **目标解析** | Issue URL / 仓库 URL / 本地路径；Issue + Docker 需 `--base-image` |
| **输出目录** | `outputs/run-{标签}-{时间戳}/`；SecBench 用环境变量 `VULN_PIPELINE_RUN_PREFIX`；纯 GitHub Issue 未设 prefix 时由 URL 推导 `owner-repo-编号` |
| **Docker 命名** | 镜像/容器前缀 `feirubei-{标签}-{时间戳}`（标签同上与 outputs 对齐） |
| **结果 JSON** | `pipeline_result.json`：`summary` 中 `compile_success` / `reproduce_success` / `fix_verified` 等 |
| **补丁导出** | `fix.patch`；容器内优先 `/src/*/.git` 再回退 `/project` |

---

## 3. 入口脚本与适用场景

| 脚本 | 典型用法 |
|------|----------|
| `run_vuln_pipeline.py` | 单入口：`TARGET` 为本地路径、仓库 HTTPS、或 Issue URL |
| `run_vuln_local.py` | 仅本地路径，`docker=False` |
| `run_vuln_docker.py` | 本地路径 + Docker 链 |
| `run_vuln_issue.py` | GitHub Issue + `allow_issue_delegate=True`；需 Docker 时 `--docker --base-image <image:tag>` |
| `run_secbench_local.py` | `--instance_id` + 可选 `--json`；与 `outputs/run-{instance_id}-{ts}` 对齐 |

---

## 4. 流水线 Agent 配置与 `mini-swe-agent`

**本仓库流水线专用 YAML**（`vuln_pipeline_core.resolve_paths` 读取）位于仓库根目录 **`config/`**：

| 文件 | 角色 |
|------|------|
| `build_agent.yaml` | Stage1：构建与审计 |
| `exploiter_agent.yaml` | Stage2：复现与 sanitizer 日志 |
| `fixer_agent.yaml` | Stage3：修复与验证 |
| `patch_agent.yaml` | Stage4：备选修复（路径约定与 `/new_project/out` 等见文件内说明） |
| `vulnerability_fix.yaml` | 单 Agent / 其它模式备用（当前流水线未引用，可手工 `mini -c` 使用） |

Docker 模式下将 **`config/`** 整目录以只读挂载到容器内 **`/agent-config/`**，各阶段通过 `-c /agent-config/<文件名>` 调用。

**上游 `mini-swe-agent`** 自带的 `default.yaml`、`mini.yaml` 等仍在 `mini-swe-agent/src/minisweagent/config/`，与上述流水线 YAML 分离。

---

## 5. 数据与产物

| 路径 | 说明 |
|------|------|
| `secbench_details.json` | SecBench 实例列表（`instance_id`、`work_dir`、`bug_description` 等） |
| `outputs/run-*` | 每次运行的 `stage*.log`、`*.traj.json`、`pipeline_result.json`、`docker-image-chain.txt`（Docker）、`post-fix-verify*.log`、`fix.patch` 等 |
| `runs/`、`run.sh` | 若存在：用于批量或示例命令列表 |

---

## 6. 文档索引

| 文档 | 内容 |
|------|------|
| `README.md` | 项目说明（可能含 SecBench 简要） |
| `QUICK_START.md` | 环境与密钥、最短命令 |
| `README_MULTI_AGENT.md` | 多 Agent 能力、参数说明 |
| `README_SECBENCH_EVAL.md` | 服务器上 SecBench 与并行示例 |
| `run_mini_agent.md` | mini / `mini-extra` 与密钥相关说明 |
| `MULTI_AGENT_WORKFLOW.md` | **本文件**：结构说明 |

---

## 7. 依赖关系简图

```
run_*.py  ──►  vuln_pipeline_core.run_pipeline()
                    │
                    ├──► Docker：docker run / commit / patch-export
                    │
                    └──► mini（容器内或本机）──► 仓库根 `config/*.yaml`
```

若需扩展新入口，保持 **`script_path` 位于仓库根目录**（与 `config/` 同级），以便 `resolve_paths` 定位到 **`config/`**。
