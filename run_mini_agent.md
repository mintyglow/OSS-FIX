# 下载并运行 mini-agent

在 Windows 上通过 **WSL** 进入 Linux 环境，再安装并运行本项目自带的 `mini-swe-agent`。

```bash
# 进入 Linux 环境
cd dir
wsl

# 创建 Linux 专用虚拟环境（名称：venv_linux）(可选)
python3 -m venv venv_linux
source venv_linux/bin/activate

或者 conda

# 安装依赖
pip install -e ./mini-swe-agent
```

安装完成后可执行 `mini --version` 确认。


**DeepSeek 有两种常见配法（二选一）：**

官方 API（以deepseek为例，环境变量名 `DEEPSEEK_API_KEY`）：

```bash
export DEEPSEEK_API_KEY="你的密钥"
export MSWEA_MODEL_NAME="deepseek/deepseek-reasoner"
```

**OpenAI 兼容接口**（`OPENAI_API_KEY` + `OPENAI_API_BASE`，与 `keys.cfg` 写法一致时须同时 export）：

```bash
export OPENAI_API_KEY="你的密钥"
export OPENAI_API_BASE="https://api.deepseek.com"
export MSWEA_MODEL_NAME="openai/deepseek-reasoner"
```

可将上述写入项目根目录的 `keys.cfg`，再执行 `source keys.cfg`（`keys.cfg` 已在 `.gitignore`，勿提交）。

**其它常见：**

```bash
export OPENAI_API_KEY="sk-..."
# 或 export ANTHROPIC_API_KEY="sk-ant-..."
```

更多运行方式见 `QUICK_START.md` 与 `README_MULTI_AGENT.md`。
