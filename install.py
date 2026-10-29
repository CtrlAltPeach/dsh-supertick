#!/usr/bin/env python3
"""Install this pack as four filesystem skills, without changing DSH itself."""
import argparse
import json
from pathlib import Path
import sys

FILES = (
    "supertick/references/STATE.MD",
    "supertick/references/ROLES.MD",
    "supertick/references/ALLOWED-MODELS.json",
    "supertick/scripts/supervisor.py",
    # Discovery entries last: do not advertise a skill before its resources exist.
    "supertick/SKILL.md",
    "supertick-interview/SKILL.md",
    "supertick-conventions/SKILL.md",
    "supertick-runs/SKILL.md",
)
NAMES = ("supertick", "supertick-interview", "supertick-conventions", "supertick-runs")


def plain(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f"refusing linked path: {parent}")
    return path


def install(target, dry_run=False):
    source = plain(Path(__file__).absolute().parent / "skills")
    target = plain(target)
    if target.exists() and not target.is_dir():
        raise ValueError("target must be a directory")
    # Preflight the complete pack before writing anything. Never overwrite skills.
    for name in NAMES:
        destination = plain(target / name)
        if destination.exists():
            raise FileExistsError(f"skill already exists: {destination}; inspect it before updating")
    license_bytes = plain(source.parent / "LICENSE").read_bytes()
    payload = [(f"{name}/LICENSE", license_bytes) for name in NAMES]
    payload += [(relative, plain(source / relative).read_bytes()) for relative in FILES]
    normalized = {name: next(data for relative, data in payload if relative == f"{name}/SKILL.md").decode("utf-8").replace("\r\n", "\n") for name in NAMES}
    for name, body in normalized.items():
        if not body.startswith("---\n") or f"\nname: {name}\n" not in body or "\ndescription: " not in body:
            raise ValueError(f"invalid skill entry: {name}")
    result = {"dry_run": dry_run, "target": str(target), "skills": list(NAMES), "files": len(payload)}
    if dry_run:
        return result
    target.mkdir(parents=True, exist_ok=True)
    # Claim all skill directories with exclusive mkdir before copying any file.
    # If the filesystem fails partway through, preserve the partial result and
    # report the error. Never remove another writer's directory as a rollback.
    for name in NAMES:
        plain(target / name).mkdir(exist_ok=False)
    for relative, data in payload:
        destination = plain(target / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=str(Path.home() / ".dsh" / "skills"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        print("Python 3.12+ required for Windows junction checks", file=sys.stderr)
        return 2
    try:
        print(json.dumps(install(args.target, args.dry_run), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "note": "No existing skill is overwritten. A filesystem failure may leave new partial directories; inspect before retry."}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
