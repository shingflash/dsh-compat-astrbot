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
from dataclasses import dataclass, field
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

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        LOG.debug("Context.%s is not emulated; returning a permissive no-op", name)
        return _AnyCallable()

    def add_llm_tools(self, *tools) -> None:
        self._llm_tools.extend(tools)

    def get_config(self) -> dict:
        return self.config


class _AnyCallable:
    """Callable, awaitable and attribute-permissive: plugins use unknown Star
    helpers as `await self.x(...)`, `self.x.y(...)` and `self.x.y.z` alike."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_a, **_k):
        return self

    def __await__(self):
        async def _result():
            return ""
        return _result().__await__()


class Star:
    def __init__(self, context: Context | None = None, config: dict | None = None):
        self.context = context or Context(config)

    def __getattr__(self, name: str):
        """AstrBot's Star base class ships helpers we do not emulate
        (html_render, text_to_image, ...). Degrade to an async no-op instead of
        crashing the plugin the first time it calls one."""
        if name.startswith("__"):
            raise AttributeError(name)
        LOG.debug("Star.%s is not emulated; returning a permissive no-op", name)
        return _AnyCallable()


class _Decorator:
    """The result of calling an unmodelled filter: applying it is a no-op."""

    def __call__(self, fn, *_a, **_k):
        return fn


class _Permissive:
    """Stand-in for an unmodelled AstrBot enum member or filter.

    `filter.PRIVATE_MESSAGE`, `filter.EventMessageType.PRIVATE_MESSAGE` and
    `@filter.permission_type(...)` all have to work from the same object.
    """

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __getitem__(self, _item):
        return self

    def __or__(self, _other):
        return self

    def __ror__(self, _other):
        return self

    def __call__(self, *_a, **_k):
        return _Decorator()


class _CommandGroup:
    """What @filter.command_group returns in AstrBot: members are declared with
    @group.command("sub"). Commands surface as "<group> <sub>"."""

    def __init__(self, name: str):
        self.name = name

    def command(self, name: str | None = None, **_kw):
        def deco(fn):
            fn.__astrbot_command__ = f"{self.name} {name or fn.__name__}"
            return fn
        return deco

    def group(self, name: str | None = None):
        return _CommandGroup(f"{self.name} {name or ''}".strip())

    def __call__(self, *a, **k):
        return None


class _Filter:
    """Record decorator metadata as function attributes; the host scans them."""

    def command(self, name: str | None = None, alias=None, **_kw):
        def deco(fn):
            fn.__astrbot_command__ = name or fn.__name__
            return fn
        return deco

    def llm_tool(self, name: str | None = None, **_kw):
        def deco(fn):
            fn.__astrbot_tool__ = name or fn.__name__
            return fn
        return deco

    def on_llm_request(self, **_kw):
        def deco(fn):
            fn.__astrbot_hook__ = "on_llm_request"
            return fn
        return deco

    def command_group(self, name: str | None = None):
        def deco(fn):
            group = _CommandGroup(name or fn.__name__)
            return group
        return deco

    def event_message_type(self, *a, **kw):
        def deco(fn):
            return fn
        return deco

    def __getattr__(self, name: str):
        """Any filter we do not model (regex, permission_type, on_decorating_result,
        after_message_sent, PRIVATE_MESSAGE, ...) must degrade to a no-op, not blow
        up the whole plugin at import time."""
        if name.startswith("__"):
            raise AttributeError(name)
        LOG.debug("filter.%s is not emulated; using a permissive stub", name)
        return _Permissive()


filter = _Filter()
logger = LOG


class StarTools:
    @staticmethod
    def get_data_dir(name: str = "") -> Path:
        """Real AstrBot derives the plugin's data dir from the caller; most plugins
        just call get_data_dir() with no argument."""
        if not name:
            import inspect as _inspect
            for frame in _inspect.stack()[1:6]:
                mod = frame.frame.f_globals.get("__name__", "")
                if mod and mod not in ("star_host", "astrbot_plugin_under_test", "__main__") and "." in mod or mod.startswith("astrbot_plugin"):
                    name = mod.split(".")[-1] if not mod.startswith("astrbot_plugin") else mod.split(".")[0]
                    break
        name = name or "default"
        p = Path.home() / ".dsh_compat_astrbot" / name
        p.mkdir(parents=True, exist_ok=True)
        return p


@dataclass
class FunctionTool:
    """Stand-in for astrbot.api.FunctionTool: plugins subclass it with @dataclass."""

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    handler: object = None


class Plain:
    def __init__(self, text: str = ""):
        self.text = str(text)

    def __str__(self) -> str:
        return self.text


class MessageEventResult(list):
    """Stand-in for astrbot.api.event.MessageEventResult: a component chain that
    real plugins build with .message() / append and return from a handler."""

    def __init__(self, chain: list | None = None):
        super().__init__(chain or [])
        self.message_str = ""

    def message(self, text: str):
        self.message_str = str(text)
        return self

    def use_t2i(self, _flag: bool = True):
        return self

    def __str__(self) -> str:
        if self.message_str:
            return self.message_str
        return "\n".join(str(c) for c in self)


class _StubModule(types.ModuleType):
    """Forgiving module: an unknown attribute becomes a permissive class.

    The hook has to live in the module dict (PEP 562); a `__getattr__` defined on
    the ModuleType subclass is never consulted by CPython.
    """

    def __init__(self, name: str):
        super().__init__(name)
        self.__path__ = []
        self.__dict__["__getattr__"] = self._auto

    def _auto(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        # a real (fake) submodule may already be registered: hand that back instead
        # of shadowing it with a stub class.
        mod = sys.modules.get(f"{self.__name__}.{name}")
        if mod is not None:
            setattr(self, name, mod)
            return mod
        LOG.debug("astrbot stub: %s.%s auto-created", self.__name__, name)

        def _init(_self, *a, **k):
            _self.args, _self.kwargs = a, k

        def _getattr(_self, item):
            raise AttributeError(item)

        def _call(_self, *a, **k):
            if len(a) == 1 and not k and (inspect.isfunction(a[0]) or inspect.ismethod(a[0])):
                return a[0]
            return _self

        class _StubMeta(type):
            # StarTools.get_data_dir(...) and friends: class-level use must work
            # both as a call and as an attribute chain (obj.method(...)).
            def __getattr__(cls, item):
                return _Permissive()

        stub = _StubMeta(name, (), {"__init__": _init, "__getattr__": _getattr, "__call__": _call})
        setattr(self, name, stub)
        return stub


class _StubLoader:
    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


class _AstrbotStubFinder:
    """Last-resort: astrbot.core.* / astrbot.api.* paths we did not model still
    import, so a plugin fails only on the feature it actually uses."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "astrbot" or fullname.startswith("astrbot."):
            import importlib.machinery
            return importlib.machinery.ModuleSpec(fullname, _StubLoader(), is_package=True)
        return None


def _install_fake_astrbot() -> None:
    api = _StubModule("astrbot.api")
    api.logger = LOG
    api.StarTools = StarTools
    api.FunctionTool = FunctionTool

    ev = _StubModule("astrbot.api.event")
    ev.AstrMessageEvent = AstrMessageEvent
    ev.MessageEventResult = MessageEventResult
    ev.filter = filter

    star = _StubModule("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    star.StarTools = StarTools

    def register(name, author="", desc="", version="", *args, **extra):
        def deco(cls):
            cls.__astrbot_meta__ = {"name": name, "author": author,
                                    "desc": desc, "version": version,
                                    **{k: v for k, v in extra.items()}}
            return cls
        return deco
    star.register = register

    prov = _StubModule("astrbot.api.provider")
    prov.ProviderRequest = ProviderRequest

    comp = _StubModule("astrbot.api.message_components")
    comp.Plain = Plain

    astrbot = _StubModule("astrbot")
    astrbot.api = api
    api.event, api.star, api.provider, api.message_components = ev, star, prov, comp

    # astrbot.api.all re-exports the public surface; `from astrbot.api.all import *`
    # is the most common import line in real plugins.
    allmod = _StubModule("astrbot.api.all")
    exported = {
        "AstrMessageEvent": AstrMessageEvent,
        "MessageEventResult": MessageEventResult,
        "filter": filter,
        "logger": LOG,
        "Plain": Plain,
        "ProviderRequest": ProviderRequest,
        "Context": Context,
        "Star": Star,
        "StarTools": StarTools,
        "register": register,
        "FunctionTool": FunctionTool,
    }
    for k, v in exported.items():
        setattr(allmod, k, v)
    extra = ["BaseMessageComponent", "MessageChain", "At", "AtAll", "Image", "Reply",
             "Record", "Video", "File", "EventMessageType", "PermissionType",
             "MessageType", "Provider", "LLMResponse", "FunctionTool", "AstrBotConfig",
             "TextPart", "ImagePart", "ToolSet"]
    for name in extra:
        getattr(allmod, name)            # auto-creates the permissive stub class
    allmod.__all__ = list(exported) + extra
    api.all = allmod

    core = _StubModule("astrbot.core")
    core_star = _StubModule("astrbot.core.star")
    core_tools = _StubModule("astrbot.core.star.star_tools")
    core_tools.StarTools = StarTools
    core_star.star_tools = core_tools
    core.star = core_star

    for mod in (astrbot, api, ev, star, prov, comp, allmod, core, core_star, core_tools):
        mod.__path__ = []          # make them packages, else astrbot.core fails
        sys.modules[mod.__name__] = mod
    if not any(isinstance(f, _AstrbotStubFinder) for f in sys.meta_path):
        sys.meta_path.append(_AstrbotStubFinder())


# ---------------------------------------------------------------- load & call


def _seed_star_imports(allmod, plugin_dir: Path) -> None:
    """`from astrbot.api.all import *` copies the module dict, so any name the
    plugin expects but we do not export becomes a NameError at import time.
    Read the plugin's own AST and pre-create the missing Capitalized names."""
    import ast

    wanted: set[str] = set()
    for py in plugin_dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        star = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and not n.level and n.module
            and n.module.startswith("astrbot") and any(a.name == "*" for a in n.names)
        ]
        if not star:
            continue
        bound = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        bound |= {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)}
        bound |= {a.asname or a.name for n in ast.walk(tree)
                  if isinstance(n, (ast.Import, ast.ImportFrom))
                  for a in n.names if a.name != "*"}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id not in bound and n.id[:1].isupper()):
                wanted.add(n.id)
    for name in wanted:
        if name not in allmod.__dict__:
            getattr(allmod, name)       # permissive stub class, via _StubModule
    allmod.__all__ = [k for k in allmod.__dict__ if not k.startswith("_")]


def load_plugin(plugin_dir: str, config: dict) -> Star:
    main_py = Path(plugin_dir) / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"main.py not found: {main_py}")
    _install_fake_astrbot()
    try:
        _seed_star_imports(sys.modules["astrbot.api.all"], Path(plugin_dir))
    except Exception as e:              # never block loading over a seeding miss
        LOG.debug("star-import seeding skipped: %s", e)
    # Load main.py as a package so relative imports ("from .utils import x")
    # resolve; they are extremely common in real AstrBot plugins.
    pkg = "astrbot_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        pkg, str(main_py), submodule_search_locations=[str(Path(plugin_dir).resolve())])
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {main_py}")
    mod = importlib.util.module_from_spec(spec)
    if str(Path(plugin_dir).resolve()) not in sys.path:
        sys.path.insert(0, str(Path(plugin_dir).resolve()))
    sys.modules[pkg] = mod
    spec.loader.exec_module(mod)

    ctx = Context(config)
    for _, obj in vars(mod).items():
        if inspect.isclass(obj) and issubclass(obj, Star) and obj is not Star:
            params = list(inspect.signature(obj.__init__).parameters)[1:]
            if "config" in params:
                return obj(ctx, config)
            return obj(ctx)
    raise RuntimeError("main.py defines no Star subclass")


def collect(inst: Star) -> dict:
    commands, tools, hooks = [], [], []
    seen: set[str] = set()
    for cls in type(inst).__mro__:
        for attr, fn in vars(cls).items():
            if attr in seen or not callable(fn):
                continue
            seen.add(attr)
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
    if isinstance(ret, (MessageResult, MessageEventResult)):
        return str(ret)
    if isinstance(ret, (list, tuple)):
        return "\n".join(_to_text(x) for x in ret if x is not None)
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
                if not info["hooks"]:
                    raise KeyError("plugin defines no on_llm_request")
                result = params.get("system_prompt", "")
                for hook in info["hooks"]:
                    result = await call_hook(inst, hook["attr"], params.get("text", ""), result)
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
