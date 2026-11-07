# chatagent

一个有固定人设的终端聊天程序：打开终端，敲一句话，和「阿岩」聊两句。

> 当前版本 v0.1.0，只跑通「读取配置 → 渲染人设 → 调用 `deepseek-flash` → 打印回复」这一条链路。
> 每轮对话互相独立，没有历史、没有流式、没有颜色。

## 30 秒上手

```powershell
# 1. 装依赖（需要 uv 与 Python 3.12）
uv sync

# 2. 设置密钥（Windows PowerShell）
$env:DEEPSEEK_API_KEY = "sk-..."

# 3. 运行
uv run chatagent
```

```bash
# macOS / Linux 第 2 步换成
export DEEPSEEK_API_KEY="sk-..."
```

进对话后输入内容回车即可，输入 `exit`（或 `quit`）退出，也可以按 `Ctrl+D`。

## 配置

| 文件 | 作用 |
| --- | --- |
| `config.toml` | 模型与运行参数：`base_url`、超时、温度、`max_tokens`、思考模式开关 |
| `persona.toml` | 人设：名字、身份、性格、说话风格、背景、行为规则、补充设定 |

两个文件都随仓库提交，由开发者维护；终端用户不需要也不应该改它们。
密钥只从环境变量 `DEEPSEEK_API_KEY` 读取，不会写入任何文件。

## 开发

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

目录结构：

```text
src/chatagent/
├── cli.py       # 入口 main()、REPL 循环
├── config.py    # config.toml 加载与校验
├── persona.py   # Persona 模型、persona.toml 加载、system prompt 渲染
├── llm.py       # DeepSeek 调用与异常映射
├── render.py    # 横幅、提示符、回复、错误输出
└── errors.py    # 项目异常体系
```
