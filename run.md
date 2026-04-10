# 运行 SecBench 评测须知
## 1. 环境要求

- **Python 3.10+**（建议 3.11/3.12）
- **Docker**（已登录、可拉取评测镜像；并行时磁盘与内存要够）
- 能访问 **LLM API** 的网络（按你配置的厂商）
- 已放置 **`secbench_details.json`**（与脚本同目录，或通过 `--json` 指定）
- 根据`secbench_details_first_80`里的项目来运行

## 2. 安装依赖

在项目根目录（本 README 所在目录）：

```bash
cd /path/to/feirubei
pip install mini-swe-agent     #若只打算跑docker不跑本地可不下
详细可参考 `run_mini_agent.md`
# 若使用 OpenAI 兼容接口，通常还需：pip install openai


```

按需复制密钥模板并填写：

```bash
cp keys.cfg.example keys.cfg
# 详见 QUICK_START.md / run_mini_agent.md

# 或直接输入命令
export DEEPSEEK_API_KEY="你的密钥"
export MSWEA_MODEL_NAME="deepseek/deepseek-reasoner"
```
建议使用linux环境，可配置应该虚拟环境：
```bash

wsl
python3 -m venv venv_linux
source venv_linux/bin/activate

```
## 3. 拉取评测镜像（示例）

每个 `instance_id` 对应一个镜像，实验前请先拉取（可写脚本批量拉取）：


```bash
docker pull hwiwonlee/secb.eval.x86_64.mruby.cve-2022-1201:patch
```

## 4. 单实例运行（运行SEC-bench80的命令）

```bash
python run_secbench_local.py \
  --instance_id mruby.cve-2022-1201 \
  --json secbench_details.json \
  --model deepseek/deepseek-reasoner \
  --timeout 1800
```
实际运行时：
python3 run_secbench_local.py --instance_id "mruby.cve-2022-1201"   （json和model使用的都是默认值）

**这就是实际的运行命令，要并行运行修改`instance_id`应该就行，在secbench_details_first_80.csv里查看其他的id**

python3 run_secbench_local.py --instance_id "mruby.cve-2022-1934"  

常用参数：

| 参数 | 说明 |
|------|------|
| `--instance_id` | **必填**，与 `secbench_details.json` 中 `instance_id` 一致 |
| `--json` | 数据文件路径，默认 `secbench_details.json` |
| `--image` | 可选；不填则自动使用 `hwiwonlee/secb.eval.x86_64.<instance_id>:patch` |
| `--model` | 传给 mini-swe-agent 的模型名 |
| `--timeout` | 单阶段超时（秒），默认 `1800` |

输出目录：`outputs/run-<instance_id>-<时间戳>/`，内含各阶段日志、`pipeline_result.json`（编译/复现/修复汇总）等。


## 5. 三种方法区别（适用范围 + 运行命令）

> 如果你只做 SEC-bench，优先使用“方法 1（SEC-bench 单实例）”。

| 方法 | 适用范围 | 运行命令（示例） |
|------|------|------|
| 方法 1：SEC-bench 单实例（推荐） | 已有 `instance_id`，并且目标镜像是 `hwiwonlee/secb.eval.x86_64.<instance_id>:patch` 这类 SEC-bench 镜像。适合按基准逐条评测。 | `python3 run_secbench_local.py --instance_id "mruby.cve-2022-1201"` |
| 方法 2：本地仓库路径 | 你手头有本地项目目录（非 SEC-bench 标准镜像），想直接在本地代码上跑三阶段。 | `python3 run_vuln_local.py test_projects/demo` |
| 方法 3：GitHub Issue + Docker | 漏洞需求来自 GitHub Issue，想让流水线自动拉取上下文并在容器里执行（通常更贴近真实修复流程）。 | `python3 run_vuln_issue.py "https://github.com/<owner>/<repo>/issues/<id>" --docker -t "Fix vulnerability described in this issue"` |

补充说明：
- 方法 1 是你当前这个文档的主线，参数以 `--instance_id` 为核心。
- 方法 2/3 的完整参数说明见 `README_MULTI_AGENT.md` 与 `QUICK_START.md`。
- 若需要批量跑 SecBench，可循环读取 `secbench_details_first_80.csv` 中的 `instance_id`。

## 6. 结果查看

- 每次运行：`outputs/run-<instance_id>-<时间戳>/pipeline_result.json`  
  - `summary.compile_success` / `reproduce_success` / `fix_verified` 等见字段说明（与 `vuln_pipeline_core` 一致）。
- 同目录下还有各阶段 `*.log`、`*.traj.json`、`docker-image-chain.txt`（Docker 模式）等。


