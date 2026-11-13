#!/usr/bin/env python3
"""star_host.py - load an AstrBot (Python) plugin behind a fake AstrBot API and
speak stdio JSON-lines.

This is not AstrBot itself; it only implements the layer plugins usually touch:
  astrbot.api.logger / event(AstrMessageEvent, filter) / star(Context, Star, register)
  astrbot.api.provider.ProviderRequest / message_components

Protocol (one JSON per line):
  request   {"id":1,"method":"list"}
            {"id":2,"method":"command","params":{"name":"selfdrive","text":"detail"}}
            {"id":3,"method":"tool","params":{"name":"t","args":{}}}
  response  {"id":1,"result":{...}} / {"id":1,"error":"..."}
  event     {"event":"ready","plugin":"...","commands":[...],"tools":[...],"hooks":[...]}
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import logging
import sys
import types
from pathlib import Path

LOG = logging.getLogger("star_host")
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="[%(levelname)s] %(message)s")

# ---------------------------------------------------------------- fake AstrBot


class MessageResult:
    def __init__(self, text: str):
        self.text = str(text)

    def __str__(self) -> str:
        return self.text


class AstrMessageEvent:
    def __init__(self, message_str: str = "", sender_id: str = "",
                 group_id: str = "", admin: bool = False):
        self.message_str = message_str
        self.sender_id = sender_id
        self.group_id = group_id
        self.unified_msg_origin = f"local:{sender_id or '0'}"
        self._admin = bool(admin)

    def plain_result(self, text: str) -> MessageResult:
        return MessageResult(text)

    def is_admin(self) -> bool:
        return self._admin

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_group_id(self) -> str:
        return self.group_id


class ProviderRequest:
    def __init__(self, system_prompt: str = ""):
        self.system_prompt = system_prompt
        self.prompt = ""
        self.contexts: list = []
        self.conversation = None


class Context:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._llm_tools: list = []

    def add_llm_tools(self, *tools) -> None:
        self._llm_tools.extend(tools)

    def get_config(self) -> dict:
        return self.config


class Star:
    def __init__(self, context: Context | None = None):
        self.context = context or Context()


class _Filter:
    """Record decorator metadata as function attributes; the host scans them."""

    def command(self, name: str | None = None):
        def deco(fn):
            fn.__astrbot_command__ = name or fn.__name__
            return fn
        return deco

    def llm_tool(self, name: str | None = None):
        def deco(fn):
            fn.__astrbot_tool__ = name or fn.__name__
            return fn
        return deco

    def on_llm_request(self):
        def deco(fn):
            fn.__astrbot_hook__ = "on_llm_request"
            return fn
        return deco

    def event_message_type(self, *a, **kw):
        def deco(fn):
            return fn
        return deco


filter = _Filter()
logger = LOG


class StarTools:
    @staticmethod
    def get_data_dir(name: str) -> Path:
        p = Path.home() / ".dsh_compat_astrbot" / name
        p.mkdir(parents=True, exist_ok=True)
        return p


class Plain:
    def __init__(self, text: str = ""):
        self.text = str(text)

    def __str__(self) -> str:
        return self.text


def _install_fake_astrbot() -> None:
    api = types.ModuleType("astrbot.api")
    api.logger = LOG
    api.StarTools = StarTools

    ev = types.ModuleType("astrbot.api.event")
    ev.AstrMessageEvent = AstrMessageEvent
    ev.filter = filter

    star = types.ModuleType("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    star.StarTools = StarTools

    def register(name, author="", desc="", version=""):
        def deco(cls):
            cls.__astrbot_meta__ = {"name": name, "author": author,
                                    "desc": desc, "version": version}
            return cls
        return deco
    star.register = register

    prov = types.ModuleType("astrbot.api.provider")
    prov.ProviderRequest = ProviderRequest

    comp = types.ModuleType("astrbot.api.message_components")
    comp.Plain = Plain

    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    for mod in (astrbot, api, ev, star, prov, comp):
        sys.modules[mod.__name__] = mod


# ---------------------------------------------------------------- load & call


def load_plugin(plugin_dir: str, config: dict) -> Star:
    main_py = Path(plugin_dir) / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"main.py not found: {main_py}")
    _install_fake_astrbot()
    spec = importlib.util.spec_from_file_location("astrbot_plugin_under_test", str(main_py))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {main_py}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["astrbot_plugin_under_test"] = mod
    sys.path.insert(0, str(Path(plugin_dir)))
    spec.loader.exec_module(mod)

    ctx = Context(config)
    for _, obj in vars(mod).items():
        if inspect.isclass(obj) and issubclass(obj, Star) and obj is not Star:
            try:
                if "config" in inspect.signature(obj.__init__).parameters:
                    return obj(ctx, config)
                return obj(ctx)
            except TypeError:
                return obj(ctx)
    raise RuntimeError("main.py defines no Star subclass")


def collect(inst: Star) -> dict:
    commands, tools, hooks = [], [], []
    for cls in type(inst).__mro__:
        for attr, fn in vars(cls).items():
            if not callable(fn):
                continue
            if hasattr(fn, "__astrbot_command__"):
                commands.append({"name": fn.__astrbot_command__, "attr": attr})
            if hasattr(fn, "__astrbot_tool__"):
                doc = inspect.getdoc(fn) or ""
                tools.append({"name": fn.__astrbot_tool__, "attr": attr,
                              "description": doc.split("\n\n")[0][:400]})
            if hasattr(fn, "__astrbot_hook__"):
                hooks.append({"name": fn.__astrbot_hook__, "attr": attr})
    return {"commands": commands, "tools": tools, "hooks": hooks}


async def _consume(ret):
    if inspect.isasyncgen(ret):
        outs = []
        async for item in ret:
            outs.append(item)
        return outs
    if inspect.iscoroutine(ret):
        return await ret
    return ret


def _to_text(ret) -> str:
    if ret is None:
        return ""
    if isinstance(ret, (list, tuple)):
        return "\n".join(_to_text(x) for x in ret if x is not None)
    if isinstance(ret, MessageResult):
        return ret.text
    return str(ret)


async def call_command(inst: Star, attr: str, text: str, admin: bool) -> str:
    fn = getattr(inst, attr)
    event = AstrMessageEvent(message_str=text, admin=admin)
    params = inspect.signature(fn).parameters
    args = [event]
    if len(params) >= 3:  # self, event, arg
        parts = text.split(maxsplit=1)
        args.append(parts[1] if len(parts) > 1 else "")
    return _to_text(await _consume(fn(*args)))


async def call_tool(inst: Star, attr: str, tool_name: str, args: dict) -> str:
    fn = getattr(inst, attr)
    event = AstrMessageEvent(admin=True)
    kwargs = dict(args or {})
    params = inspect.signature(fn).parameters
    if "event" in params:
        kwargs["event"] = event
    return _to_text(await _consume(fn(**kwargs)))


async def call_hook(inst: Star, attr: str, text: str, system_prompt: str) -> str:
    fn = getattr(inst, attr)
    event = AstrMessageEvent(message_str=text)
    req = ProviderRequest(system_prompt)
    await _consume(fn(event, req))
    return req.system_prompt


# ---------------------------------------------------------------- stdio loop


def write_frame(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def main_async(args) -> int:
    cfg = {}
    if args.config:
        try:
            cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        except Exception as e:
            write_frame({"event": "fatal", "error": f"cannot read config: {e}"})
            return 2
    try:
        inst = load_plugin(args.plugin, cfg)
        info = collect(inst)
    except Exception as e:
        write_frame({"event": "fatal", "error": f"{type(e).__name__}: {e}"})
        return 2

    write_frame({"event": "ready", "plugin": Path(args.plugin).name,
                 "commands": [c["name"] for c in info["commands"]],
                 "tools": [t["name"] for t in info["tools"]],
                 "hooks": [h["name"] for h in info["hooks"]]})

    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        try:
            if method == "list":
                result = {"plugin": Path(args.plugin).name, **info}
            elif method == "command":
                target = next((c for c in info["commands"] if c["name"] == params.get("name")), None)
                if not target:
                    raise KeyError(f"no such command: {params.get('name')}")
                result = await call_command(inst, target["attr"], params.get("text", ""),
                                            bool(params.get("admin")))
            elif method == "tool":
                target = next((t for t in info["tools"] if t["name"] == params.get("name")), None)
                if not target:
                    raise KeyError(f"no such tool: {params.get('name')}")
                result = await call_tool(inst, target["attr"], params.get("name"),
                                         params.get("args") or {})
            elif method == "hook":
                target = info["hooks"][0] if info["hooks"] else None
                if not target:
                    raise KeyError("plugin defines no on_llm_request")
                result = await call_hook(inst, target["attr"], params.get("text", ""),
                                         params.get("system_prompt", ""))
            elif method == "shutdown":
                write_frame({"id": rid, "result": True})
                break
            else:
                raise KeyError(f"unknown method: {method}")
            write_frame({"id": rid, "result": result})
        except Exception as e:
            write_frame({"id": rid, "error": f"{type(e).__name__}: {e}"})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--config", default="")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
