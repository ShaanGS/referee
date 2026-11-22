"""Signed evidence: SHA-256 manifest of every file, signed with an ed25519 key
that lives outside the workspace (~/.referee). Uses `ssh-keygen -Y`, so no
extra dependencies."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HOME = Path.home() / ".referee"
KEY = HOME / "signing_key"
ALLOWED = HOME / "allowed_signers"
NAMESPACE = "referee-evidence"
IDENTITY = "referee"
MANIFEST = "manifest.json"


class SignError(RuntimeError):
    pass


def ensure_key() -> Path:
    if not KEY.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "referee-evidence", "-f", str(KEY)],
                       check=True)
        pub = (KEY.with_suffix(".pub") if KEY.with_suffix(".pub").exists() else Path(str(KEY) + ".pub")).read_text()
        ALLOWED.write_text(f'{IDENTITY} namespaces="{NAMESPACE}" {pub}')
    return KEY


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# Format 2 (current): the manifest covers every file except this folder's own manifest and
# signature, so the seals of nested packs (agent rounds) are hashed into the outer one, and
# "sealed_children" lists each nested seal recorded the moment it was made. Format 1 (no
# "format" key) skipped manifests at every depth; it is still verified the old way.
FORMAT = 2


def _files(ev: Path, fmt: int = FORMAT) -> list[Path]:
    if fmt >= 2:
        own = {ev / MANIFEST, ev / (MANIFEST + ".sig")}
        return sorted(p for p in ev.rglob("*") if p.is_file() and p not in own)
    return sorted(p for p in ev.rglob("*")
                  if p.is_file() and p.name not in (MANIFEST, MANIFEST + ".sig"))


def manifest_sha(ev: Path) -> str:
    return _sha(ev / MANIFEST)


def sign_dir(ev: Path, children: dict[str, str] | None = None) -> Path:
    """Seal ev. children: {relative dir: sha256 of its manifest.json} for nested packs sealed
    earlier; verify then fails if any of them is missing, replaced or broken."""
    ensure_key()
    (ev / "signer.pub").write_text(Path(str(KEY) + ".pub").read_text())
    manifest = {"format": FORMAT,
                "files": {str(p.relative_to(ev)).replace("\\", "/"): _sha(p) for p in _files(ev)},
                "sealed_children": dict(sorted((children or {}).items()))}
    (ev / MANIFEST).write_text(json.dumps(manifest, indent=1, sort_keys=True))
    sig = ev / (MANIFEST + ".sig")
    sig.unlink(missing_ok=True)
    p = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(KEY), "-n", NAMESPACE, str(ev / MANIFEST)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SignError(p.stderr.strip())
    return sig


def verify_dir(ev: Path, allowed: Path = ALLOWED) -> tuple[bool, list[str]]:
    """Returns (ok, problems). Checks the signature, then every file hash."""
    problems = []
    man, sig = ev / MANIFEST, ev / (MANIFEST + ".sig")
    if not man.exists() or not sig.exists():
        return False, ["no manifest/signature"]
    with man.open("rb") as fh:
        p = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", IDENTITY, "-n", NAMESPACE,
                            "-s", str(sig)], stdin=fh, capture_output=True, text=True)
    if p.returncode != 0:
        problems.append("signature invalid: " + (p.stderr or p.stdout).strip())
    data = json.loads(man.read_text())
    fmt = int(data.get("format", 1))
    listed = data["files"]
    present = {str(f.relative_to(ev)).replace("\\", "/") for f in _files(ev, fmt)}
    for rel, h in listed.items():
        f = ev / rel
        if not f.exists():
            problems.append(f"missing: {rel}")
        elif _sha(f) != h:
            problems.append(f"modified: {rel}")
    for rel in sorted(present - set(listed)):
        problems.append(f"added after signing: {rel}")
    for rel, h in (data.get("sealed_children") or {}).items():
        child = ev / rel / MANIFEST
        if not child.exists():
            problems.append(f"{rel}: seal missing (it was sealed during the run)")
        elif _sha(child) != h:
            problems.append(f"{rel}: seal replaced since it was made")
    for sub in sorted(ev.rglob(MANIFEST)):          # nested packs (e.g. agent rounds) carry their own seal
        if sub.parent != ev:
            ok, sub_problems = verify_dir(sub.parent, allowed)
            problems += [f"{sub.parent.relative_to(ev).as_posix()}: {p}" for p in sub_problems]
    return not problems, problems
