#!/usr/bin/env python3
"""Claude Code hook (Stop and SessionEnd): writes what was done and the next move to logs/work.jsonl.

A hook has no AI, so this reads what the session already left in its transcript: commit
messages, the session record it wrote, and an explicit 次の一手： line in the last reply.
Sessions that only read and changed nothing write nothing. It always exits 0 and prints
nothing, because exit code 2 from a Stop hook would keep the session from ending.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import logs, tools  # noqa: E402
from lifeos.lib.tools import load_tool  # noqa: E402

LOG = "work"
TAIL_BYTES = 5 * 1024 * 1024  # this runs after every turn, and long transcripts are large
TEXT_MAX = 200
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
SESSION_FILE = re.compile(r"memory/episodic/sessions/[^\s'\"]+\.md")
COMMIT = re.compile(r"^\[[^\]\n]+ [0-9a-f]{7,40}\] (.+)$", re.M)
TAGGED = re.compile(r"<([\w-]+)[^>]*>.*?</\1>", re.S)
NEXT_LINE = re.compile(r"^[^\w\n]*(?:次の一手|次にやること|次は|次)\s*\**\s*[:：]\s*\**\s*(.+?)\s*\**\s*$", re.M)
NEXT_HEAD = re.compile(r"^#+\s*次の一手\s*$\n+[^\w\n]*(.+?)\s*$", re.M)
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,}"
                    r"|github_pat_[A-Za-z0-9_]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|\b\d{6,}:[A-Za-z0-9_-]{30,}")


def clean(text):
    text = " ".join(SECRET.sub("（伏せ字）", str(text or "")).split())
    return text[:TEXT_MAX]


def _blocks(message):
    content = (message or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""


def _read_lines(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            f.readline()  # the first line is cut in half
        for raw in f:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(d, dict):
                yield d


def read_transcript(path):
    """What the main conversation did. Subagents' lines are skipped."""
    out = {"prompt": "", "last_text": "", "edited": [], "commits": [], "session_files": [], "branch": ""}
    bash = {}
    for d in _read_lines(path):
        if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
            continue
        out["branch"] = d.get("gitBranch") or out["branch"]
        for b in _blocks(d.get("message")):
            kind = b.get("type")
            if d["type"] == "assistant" and kind == "text" and b.get("text", "").strip():
                out["last_text"] = b["text"]
            elif kind == "tool_use":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                if b.get("name") in EDIT_TOOLS and inp.get("file_path"):
                    if inp["file_path"] not in out["edited"]:
                        out["edited"].append(inp["file_path"])
                    out["session_files"] += SESSION_FILE.findall(inp["file_path"])
                elif b.get("name") == "Bash":
                    cmd = str(inp.get("command") or "")
                    bash[b.get("id")] = cmd
                    out["session_files"] += SESSION_FILE.findall(cmd)
            elif kind == "tool_result" and "git commit" in bash.get(b.get("tool_use_id"), ""):
                out["commits"] += COMMIT.findall(_result_text(b))
            elif d["type"] == "user" and kind == "text" and not out["prompt"] and not d.get("isMeta"):
                out["prompt"] = next((x.strip() for x in TAGGED.sub("", b.get("text", "")).splitlines() if x.strip()), "")
    return out


def explicit_next(text):
    """Only a line that says it is the next move. Guessing from prose would mislead later."""
    found = NEXT_LINE.findall(text or "") or NEXT_HEAD.findall(text or "")
    return found[-1] if found else ""


def session_record(session_files, core):
    """The session record this conversation wrote, read the way Desk reads it."""
    if not session_files:
        return None
    wanted = session_files[-1]
    for s in core.memory_doc().get("sessions") or []:
        if str(s.get("file") or "").replace(os.sep, "/").endswith(wanted):
            return s
    return None


def summarize(t, core):
    """{done, next}, or None when the session changed nothing and named no next move."""
    rec = session_record(t["session_files"], core)
    nxt = explicit_next(t["last_text"]) or core.hitokoto(rec, "次")
    done = core.hitokoto(rec, "いま") or "、".join(t["commits"][-3:])
    if not done and t["edited"]:
        done = f"{t['prompt'] or '作業'}（{len(t['edited'])}個のファイルを変更）"
    if not done and nxt:
        done = t["prompt"]
    if not done and not nxt:
        return None
    return {"done": clean(done), "next": clean(nxt)}


def run(event):
    """Writes at most one record and returns it. Stop fires every turn, so repeats are skipped."""
    tool_dir = os.path.dirname(os.path.abspath(__file__))
    if tools.info(tool_dir).get("status") == "stopped":
        return None
    path = event.get("transcript_path")
    if not path or not os.path.exists(path):
        return None
    t = read_transcript(path)
    summary = summarize(t, load_tool("15-resume", "core"))
    if not summary:
        return None
    sid = event.get("session_id") or ""
    mine = [r for r in logs.read(LOG, limit=200) if r.get("session") == sid]
    if mine and mine[-1].get("done") == summary["done"] and mine[-1].get("next") == summary["next"]:
        return None
    return logs.append(LOG, {"session": sid, "cwd": event.get("cwd") or "", "branch": t["branch"],
                             "event": event.get("hook_event_name") or "", **summary,
                             "files": [os.path.basename(p) for p in t["edited"][-10:]]})


def main():
    try:
        run(json.loads(sys.stdin.read() or "{}"))
    except Exception as e:  # a broken log must never get in the way of the session
        try:
            logs.append("work_errors", {"error": type(e).__name__})
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
