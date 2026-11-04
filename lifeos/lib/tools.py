"""Finds the tools in lifeos/NN-slug/ and loads their Python files.

Folder names start with a digit and contain a hyphen, so `import` cannot reach them:
use load_tool("04-voice", "core") instead.
"""
import glob
import hashlib
import importlib.util
import json
import os
import sys

from lifeos.lib import paths


def root_dir():
    return os.environ.get("LIFEOS_TOOLS_ROOT") or os.path.join(paths.REPO, "lifeos")


def tool_dirs(root=None):
    return sorted(d for d in glob.glob(os.path.join(root or root_dir(), "[0-9][0-9]-*")) if os.path.isdir(d))


def info(tool_dir):
    """The tool's tool.json, or {} if it is missing or broken."""
    try:
        with open(os.path.join(tool_dir, "tool.json"), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def load_file(tool_dir, name):
    """Runs lifeos/NN-slug/<name>.py once and returns the module."""
    path = os.path.join(tool_dir, name + ".py")
    key = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:8]
    mod_name = "lifeos_tool_" + os.path.basename(tool_dir).replace("-", "_") + "_" + name + "_" + key
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[mod_name]
        raise
    return mod


def load_tool(slug, name):
    return load_file(os.path.join(root_dir(), slug), name)


def brief_parts(day=None, root=None):
    """What each tool wants in the morning brief: [(slug, [lines])], from NN-slug/brief.py.

    A brief.py defines lines(day) -> list of strings. One failing tool never stops the brief.
    """
    day = day or paths.today()
    out = []
    for d in tool_dirs(root):
        if not os.path.exists(os.path.join(d, "brief.py")) or info(d).get("status") == "stopped":
            continue
        try:
            lines = [str(x) for x in (load_file(d, "brief").lines(day) or [])]
        except Exception:
            continue
        if lines:
            out.append((os.path.basename(d), lines))
    return out
