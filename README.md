# chatagent

一个装在终端里的聊天搭子：敲 `chatagent`，就和「阿岩」聊起来。有固定人设、记得这次会话说过的话、回复逐字出现。

对话只在内存里，关掉即清空——不存盘，也不上传。

## 安装

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行一次：

```powershell
uv tool install .
```

之后 `chatagent` 全局可用，在任意目录都能启动。

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

## 关于人设

它是谁、怎么说话，由随包发布的 `persona.toml` 决定，作者写好的，终端用户不需要也不应该改。

想换一个，改 `persona.toml` 后重新 `uv tool install .`；在仓库目录里直接 `uv run chatagent` 时，当前目录的同名文件优先。
