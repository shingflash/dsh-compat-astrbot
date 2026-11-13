# dsh-compat-astrbot

在 **dsh / Node 侧加载 AstrBot（Python）插件**，不用装 AstrBot 本体。

## 原理

`py/star_host.py` 用一层假 `astrbot` 包（`astrbot.api` / `event` / `star` /
`provider` / `message_components`）把插件 `main.py` 拉起来：

- `@register` 装饰的类 → 实例化；
- `@filter.command` → 可调用命令；
- `@filter.llm_tool` → 可调用工具；
- `@filter.on_llm_request` → 提示注入钩子。

Node 侧 `index.mjs` 只做进程与协议转发，一行一个 JSON。

## 用法

```bash
# 看插件里有什么
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --probe

# RPC 模式（给 dsh 插件或任意客户端接）
node index.mjs --plugin ./path/to/astrbot_plugin_xxx --rpc
```

请求：

```json
{"id":1,"method":"list"}
{"id":2,"method":"command","params":{"name":"自迭代","text":"详细"}}
{"id":3,"method":"tool","params":{"name":"self_iter_status","args":{}}}
{"id":4,"method":"hook","params":{"text":"用户这轮说的话","system_prompt":"..."}}
{"id":5,"method":"shutdown"}
```

## 参数

| 参数 | 说明 |
|---|---|
| `--plugin` | AstrBot 插件目录（含 `main.py`），必填 |
| `--config` | 传给插件的配置 JSON 文件 |
| `--python` | Python 解释器，默认 `PYTHON` 环境变量或 `python3` |
| `--probe` | 打印插件暴露的命令/工具后退出 |
| `--rpc` | 进 stdio JSON-lines RPC 模式 |
| `--debug` | 把 Python 侧日志转发到 stderr |

## 限制

只覆盖 AstrBot 插件最常用的一层 API：没有 WebUI、没有数据库、没有平台适配器、
没有 `Context` 的全部能力。插件依赖这些的部分会退化或失败，属于预期。

## 另外一半

反方向（AstrBot 吃 dsh 插件）在本组织的 `astrbot_plugin_compat_dsh`。
