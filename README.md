# chatagent

一个装在终端里的聊天搭子：敲 `chatagent`，就和「小六」聊起来——它是温州市具身智能6S中心大厅里的迎宾机器狗，负责接待、导览和讲解展项。有固定人设、记得这次会话说过的话、回复逐字出现。

对话只在内存里，关掉即清空——不存盘，也不上传。

## 安装

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行一次：

```powershell
uv tool install .
```

之后 `chatagent` 和 `chatagent-robot` 全局可用，在任意目录都能启动。

## 设置 API Key

密钥只从环境变量读取，不会写入任何文件。

```powershell
# Windows PowerShell（只在当前窗口有效）
$env:DEEPSEEK_API_KEY = "sk-..."
```

```bash
# macOS / Linux
export DEEPSEEK_API_KEY="sk-..."
```

想让它在每次打开终端时都生效，把这行加进 PowerShell 的 `$PROFILE` 或 `~/.bashrc`。

> **费用**：每条消息按 DeepSeek 官方价格从你自己的账户扣费，本程序不代收、不转卖。不说话就不花钱。

## 开始聊

```powershell
chatagent
```

看到方框横幅后，在 `你 › ` 后面输入内容、回车发送。它会记得这次会话里说过的话。

## 一次输入多行

贴报错、贴代码时，在每行末尾敲一个 `\` 再回车，提示符会变成 `   ... `，最后一行直接回车，整段一次发出。

行尾想保留一个真正的反斜杠，就敲两个 `\\`。

## 中断、重试与退出

- 生成到一半想打断：按 `Ctrl+C`，只打断这一轮，对话继续。
- 网络抖动或服务端繁忙：自动重试 2 次，仍失败就提示一句中文，程序不退出。
- 退出：输入 `exit` 或 `quit`，或按 `Ctrl+D`；空闲时按 `Ctrl+C` 也可以。

## 连接机器狗（可选）

`chatagent-robot` 在原聊天能力上增加工具调用，可以查询状态、移动、原地转向、
改变姿态、停止和急停。它只通过 HTTP 访问 WSL2 里的 `agent_control` 服务，
Windows 侧不需要安装 ROS2 或 SDK。

先启动 WSL2 的 `agent_control`（默认地址 `http://127.0.0.1:8100`），再运行：

```powershell
chatagent-robot
```

默认读取 `config.toml` 的 `[robot]` 段。需要连接别的地址或服务要求 token 时，
用环境变量覆盖，token 不要写进文件：

```powershell
$env:AGENT_CONTROL_BASE_URL = "http://192.168.0.48:8100"
$env:AGENT_CONTROL_TOKEN = "..."   # 仅当服务绑定非回环地址并启用鉴权
chatagent-robot
```

支持的动作：前进/后退、左右平移、原地左转/右转、站立/趴下/平衡/移动模式/锁定、
停止、软件急停与解除。左/右在移动工具里是**平移**，不是转向；模型会先追问缺失的
距离或角度，并且一次只执行一个动作。

### 安全须知

- 软件急停依赖控制权、网络和程序存活，**不能替代实体遥控器和断电**。首次真机测试
  必须清空场地、让机器狗站稳，并由人在旁边手持实体遥控器，从 0.1–0.2 米、10–20°
  开始。
- 工具执行或轮询时按 `Ctrl+C` 会调用 `/api/stop`，并把 `interrupted` 结果写回对话。
- `robot_clear_estop` 会让机器狗重新站立，终端里必须输入 `y` 二次确认。
- 工具失败、超时、中止或状态过期时，回复会如实给出错误和已知的实际测量值，不会
  伪造“已成功”。实际位移以机器狗里程计为准，可能有厘米级误差。
- 实现阶段的自动化测试只使用本地 mock 服务和 mock 模型流，不连接真实机器狗，也不
  调用真实 DeepSeek API。

## 关于人设

它是谁、怎么说话，由随包发布的 `persona.toml` 决定，作者写好的，终端用户不需要也不应该改。

想换一个，改 `persona.toml` 后重新 `uv tool install .`；在仓库目录里直接 `uv run chatagent` 时，当前目录的同名文件优先。
