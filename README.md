<div align="center">

# 🧩 dsh-compat-astrbot

**在 dsh / Node 里直接加载 AstrBot（Python）插件 —— 不用装 AstrBot。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Node](https://img.shields.io/badge/Node-18%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Verified](https://img.shields.io/badge/verified-Node%20v24.21.0-success)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/shingflash/dsh-compat-astrbot/pulls)

[English](#-english) · [中文](#-中文)

</div>

---

## 📖 English

### How it works

`py/star_host.py` installs a thin fake `astrbot` package (`astrbot.api` / `event` / `star` / `provider` / `message_components`) and loads the plugin's `main.py`:

- classes decorated with `@register` are instantiated;
- `@filter.command` functions become callable commands;
- `@filter.llm_tool` functions become callable tools;
- `@filter.on_llm_request` functions become prompt hooks.

`index.mjs` only handles the subprocess and the JSON-lines protocol.

### ✨ Features

- **No AstrBot install required** — a fake `astrbot` package does the job
- **Probe mode** — dump a plugin's commands / tools / hooks and exit
- **Long-lived RPC** — JSON-lines over stdio, ready for an agent to drive
- **Config injection** — pass any JSON as the plugin's config

### 📦 Usage

```bash
# inspect what a plugin exposes
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --probe

# JSON-lines RPC mode
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --rpc
```

Requests:

```json
{"id":1,"method":"list"}
{"id":2,"method":"command","params":{"name":"selfdrive","text":"detail"}}
{"id":3,"method":"tool","params":{"name":"self_iter_status","args":{}}}
{"id":4,"method":"hook","params":{"text":"user turn","system_prompt":"..."}}
{"id":5,"method":"shutdown"}
```

### ⚙️ Options

| Option | Description |
|---|---|
| `--plugin` | AstrBot plugin directory containing `main.py` (required) |
| `--config` | JSON file passed to the plugin as config |
| `--python` | Python interpreter; otherwise `PYTHON`, then `py -3`, `python3`, `python` |
| `--probe` | Print commands/tools/hooks and exit |
| `--rpc` | Enter stdio JSON-lines RPC mode |
| `--debug` | Forward Python-side logs to stderr |

### ✅ Verified

Tested with Node v24.21.0 against real AstrBot plugins: `probe`, `list`, `command` and `tool` calls all return expected output.

### ⚠️ Limitations

Only the commonly used AstrBot plugin surface is emulated: no web UI, no database layer, no platform adapters, no full `Context`. Plugins relying on those degrade or fail for that part.

### 🔁 The other half

For the reverse direction (AstrBot loading dsh plugins) see [`astrbot_plugin_compat_dsh`](https://github.com/shingflash/astrbot_plugin_compat_dsh).

---

## 📖 中文

### 原理

`py/star_host.py` 装一套极薄的假 `astrbot` 包（`astrbot.api` / `event` / `star` / `provider` / `message_components`），然后加载插件的 `main.py`：

- 带 `@register` 的类会被实例化；
- `@filter.command` 函数变成可调命令；
- `@filter.llm_tool` 函数变成可调工具；
- `@filter.on_llm_request` 函数变成提示词钩子。

`index.mjs` 只负责子进程和 JSON-lines 协议。

### ✨ 特点

- **不用装 AstrBot** —— 一套假 `astrbot` 包就够
- **探测模式** —— 直接列出某个插件的命令 / 工具 / 钩子
- **常驻 RPC** —— stdio 上的 JSON-lines，方便 agent 驱动
- **配置注入** —— 任意 JSON 都能作为插件配置传进去

### 📦 用法

```bash
# 看看某个插件暴露出什么
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --probe

# JSON-lines RPC 模式
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --rpc
```

请求示例：

```json
{"id":1,"method":"list"}
{"id":2,"method":"command","params":{"name":"selfdrive","text":"detail"}}
{"id":3,"method":"tool","params":{"name":"self_iter_status","args":{}}}
{"id":4,"method":"hook","params":{"text":"用户这一轮","system_prompt":"..."}}
{"id":5,"method":"shutdown"}
```

### ⚙️ 参数

| 参数 | 说明 |
|---|---|
| `--plugin` | 含 `main.py` 的 AstrBot 插件目录（必填） |
| `--config` | 作为插件配置传入的 JSON 文件 |
| `--python` | Python 解释器；否则依次尝试 `PYTHON`、`py -3`、`python3`、`python` |
| `--probe` | 打印命令/工具/钩子后退出 |
| `--rpc` | 进入 stdio JSON-lines RPC 模式 |
| `--debug` | 把 Python 侧日志转发到 stderr |

### ✅ 已验证

在 Node v24.21.0 上对着真实 AstrBot 插件测过：`probe`、`list`、`command`、`tool` 调用都返回预期结果。

### ⚠️ 限制

只模拟了 AstrBot 插件的常用面：没有 WebUI、没有数据库层、没有平台适配器、没有完整的 `Context`。依赖这些的插件会降级或那部分直接失败。

### 🔁 另一半

反方向（AstrBot 加载 dsh 插件）见 [`astrbot_plugin_compat_dsh`](https://github.com/shingflash/astrbot_plugin_compat_dsh)。

---

<div align="center">

### 📄 License

[MIT](LICENSE) · 有问题欢迎开 issue / PR

</div>
