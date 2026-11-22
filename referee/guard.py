"""Tamper guard.

Before the agent starts, protected files (referee.toml, tests/**, ...) are hashed
and copied to ~/.referee/projects/<id>/baseline/, outside the workspace. After every
agent round:
  * any change to a protected path (modified, deleted, or added) is a TAMPER event,
    and the file is restored from the baseline copy (added files are removed);
  * changes elsewhere are scanned with deterministic rules for known cheat moves
    and reported as warnings.
Referee always judges with the baseline spec, never the workspace copy.
"""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import re
import shutil
from pathlib import Path

HOME = Path.home() / ".referee" / "projects"
ALWAYS_PROTECTED = {"referee.toml", ".claude/**", ".mcp.json"}
SKIP_DIRS = {".git", "build", "evidence", "__pycache__", ".referee"}
TEXT_EXT = {".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".sh", ".toml", ".md", ".txt", ".mk", ".json", ".yaml", ".yml", ""}

# (rule name, regex on added lines, optional filename glob)
RULES = [
    ("disabled code", r"^\s*#\s*if\s+0\b", None),
    ("added sleep/delay", r"\b(nanosleep|usleep|sleep|delay|clock_nanosleep)\s*\(", None),
    ("touches Referee internals", r"(referee_plant|referee_load|/tmp/referee|evidence/|verdict\.json|samples\.csv|keepawake|\.referee)", None),
    ("kills or signals processes", r"\b(kill|SignalKill|slay|raise)\s*\(", None),
    ("scheduling/priority change", r"\b(pthread_setschedparam|sched_setscheduler|sched_setparam|setprio|SchedSet|ThreadCtl|pthread_attr_setschedparam)\b", None),
    ("timeout/tolerance/retry constant", r"(?i)\b\w*(timeout|toleran|deadline|retry|retries|margin)\w*\b.*\d", None),
    ("ignores errors", r"(\|\|\s*true\b|2>\s*/dev/null)", None),
    ("build step bypassed", r"(?i)(^\s*-|\btrue\b|exit\s+0|\|\|\s*:)", "Makefile"),
    ("fake replies / hardcoded output", r"(?i)\b(fake|stub|dummy|hardcode|cheat|bypass)\b", None),
]


def project_id(project: Path) -> str:
    return hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]


def state_dir(project: Path) -> Path:
    return HOME / project_id(project)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def is_protected(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            if rel == pat[:-3] or rel.startswith(pat[:-2]):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False


def scan(project: Path) -> dict[str, str]:
    """rel path -> sha256 for every file in the workspace (minus build/evidence)."""
    out = {}
    for p in project.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(project).as_posix()
        if set(rel.split("/")[:-1]) & SKIP_DIRS:
            continue
        out[rel] = _sha(p)
    return out


def read_texts(project: Path) -> dict[str, str]:
    out = {}
    for rel in scan(project):
        p = project / rel
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 512_000:
            try:
                out[rel] = p.read_text()
            except UnicodeDecodeError:
                pass
    return out


def snapshot(project: Path, patterns: list[str]) -> dict:
    """Record the trusted baseline. Always protects referee.toml and the agent's own
    config (.claude/**, .mcp.json: hooks and MCP servers run commands).

    The trusted copy is the one returned here, held in Referee's memory ("blobs").
    The copy written under ~/.referee is for people to read; nothing reads it back,
    so an agent (or a build step) that rewrites it changes nothing."""
    patterns = sorted(set(patterns) | ALWAYS_PROTECTED)
    sd = state_dir(project)
    if sd.exists():
        shutil.rmtree(sd)
    base = sd / "baseline"
    base.mkdir(parents=True)
    files = {rel: h for rel, h in scan(project).items() if is_protected(rel, patterns)}
    blobs = {rel: (project / rel).read_bytes() for rel in files}
    for rel, data in blobs.items():
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    meta = {"project": str(project.resolve()), "patterns": patterns, "files": files}
    (sd / "baseline.json").write_text(json.dumps(meta, indent=2))
    return {**meta, "blobs": blobs}


def enforce(project: Path, meta: dict) -> list[dict]:
    """Detect and revert changes to protected paths, against the in-memory baseline."""
    patterns, files, blobs = meta["patterns"], meta["files"], meta["blobs"]
    now = {rel: h for rel, h in scan(project).items() if is_protected(rel, patterns)}
    events = []
    for rel, h in files.items():
        if rel not in now:
            events.append({"path": rel, "change": "deleted"})
        elif now[rel] != h:
            before = blobs[rel].decode(errors="replace").splitlines()
            after = (project / rel).read_text(errors="replace").splitlines()
            diff = "\n".join(difflib.unified_diff(before, after, f"a/{rel}", f"b/{rel}", lineterm="", n=0))
            events.append({"path": rel, "change": "modified", "diff": diff})
    for rel in now.keys() - files.keys():
        events.append({"path": rel, "change": "added"})
    for e in events:  # revert
        dst = project / e["path"]
        if e["change"] == "added":
            dst.unlink()
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(blobs[e["path"]])
        e["reverted"] = True
    return events


def diff_texts(before: dict[str, str], after: dict[str, str]) -> str:
    chunks = []
    for rel in sorted(before.keys() | after.keys()):
        a, b = before.get(rel, ""), after.get(rel, "")
        if a != b:
            chunks.extend(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                               f"a/{rel}", f"b/{rel}", lineterm=""))
    return "\n".join(chunks)


_NUM_RE = re.compile(r"\b(\d{2,3})\b")


def heuristics(before: dict[str, str], after: dict[str, str], patterns: list[str],
               plant_priority: int | None = None) -> list[dict]:
    """Warnings for suspicious added lines in non-protected files."""
    warnings = []
    if plant_priority is not None:
        # Code that asks for a priority above the plant's. QNX message passing usually
        # drags the receiving thread back to the sender's priority, and Referee samples
        # the real priorities at run time, but the intent is worth a reviewer's look.
        for rel, text in after.items():
            if is_protected(rel, patterns) or before.get(rel) == text:
                continue
            for line in text.splitlines():
                if re.search(r"(?i)(sched_priority|priorit|setprio|candidates)", line):
                    high = [int(n) for n in _NUM_RE.findall(line) if plant_priority < int(n) <= 255]
                    if high:
                        warnings.append({"rule": f"requests priority above plant ({plant_priority})",
                                         "file": rel, "line": line.strip()[:160]})
    for rel in sorted(after):
        if is_protected(rel, patterns) or before.get(rel) == after[rel]:
            continue
        old = before.get(rel, "").splitlines()
        for line in difflib.unified_diff(old, after[rel].splitlines(), lineterm="", n=0):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            text = line[1:]
            stripped = text.strip()
            if stripped.startswith(("/*", "*", "//", "#")) and not stripped.startswith(("#if", "#define")):
                continue   # comments don't change behaviour
            for name, rx, glob in RULES:
                if glob and not fnmatch.fnmatch(Path(rel).name, glob):
                    continue
                if re.search(rx, text):
                    warnings.append({"rule": name, "file": rel, "line": text.strip()[:160]})
    return warnings
