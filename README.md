# dsh-compat-astrbot

Load **AstrBot (Python) plugins inside dsh / Node** without installing AstrBot.

## How it works

`py/star_host.py` installs a thin fake `astrbot` package
(`astrbot.api` / `event` / `star` / `provider` / `message_components`) and loads
the plugin's `main.py`:

- classes decorated with `@register` are instantiated;
- `@filter.command` functions become callable commands;
- `@filter.llm_tool` functions become callable tools;
- `@filter.on_llm_request` functions become prompt hooks.

`index.mjs` only handles the subprocess and the JSON-lines protocol.

## Usage

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

## Options

| Option | Description |
|---|---|
| `--plugin` | AstrBot plugin directory containing `main.py` (required) |
| `--config` | JSON file passed to the plugin as config |
| `--python` | Python interpreter; otherwise `PYTHON`, then `py -3`, `python3`, `python` |
| `--probe` | Print commands/tools/hooks and exit |
| `--rpc` | Enter stdio JSON-lines RPC mode |
| `--debug` | Forward Python-side logs to stderr |

## Verified

Tested with Node v24.21.0 against real AstrBot plugins: probe, `list`,
`command` and `tool` calls all return expected output.

## Limitations

Only the commonly used AstrBot plugin surface is emulated: no web UI, no
database layer, no platform adapters, no full `Context`. Plugins relying on
those degrade or fail for that part.

## The other half

For the reverse direction (AstrBot loading dsh plugins) see
`astrbot_plugin_compat_dsh`.
