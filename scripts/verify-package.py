"""Read-only archive/source comparison; development check, not a runtime dependency."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
archive = Path(sys.argv[1]).resolve()
required = {"package.json", "index.js", "watch.mjs", "monitor.mjs", "cordis.patch.yml", "README.MD", "LICENSE"}
required.update(f"skills/{name}/SKILL.md" for name in ("supertick", "supertick-interview", "supertick-conventions"))
seen = set()
with tarfile.open(archive, "r:gz") as bundle:
    for item in bundle.getmembers():
        path = PurePosixPath(item.name)
        assert path.parts[0] == "package" and ".." not in path.parts, item.name
        if item.isdir():
            continue
        assert item.isfile(), f"non-regular package member: {item.name}"
        relative = path.relative_to("package").as_posix()
        assert relative not in seen, f"duplicate member: {relative}"
        assert not ({"tests", "SUPERTICK", "RUNS", "__pycache__"} & set(path.parts)), relative
        assert not relative.endswith((".pyc", ".pyo")), relative
        source = root.joinpath(*PurePosixPath(relative).parts)
        assert source.is_file(), relative
        packaged = bundle.extractfile(item).read()
        if relative == "package.json":
            # pnpm normalizes whitespace/key order in published metadata.
            assert json.loads(packaged) == json.loads(source.read_bytes()), "package metadata mismatch"
        else:
            assert packaged == source.read_bytes(), f"source mismatch: {relative}"
        seen.add(relative)
assert required <= seen, f"missing members: {sorted(required - seen)}"
print(json.dumps({"status": "PASS", "files": len(seen), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "source_bytes_match_except_normalized_manifest": True, "manifest_semantically_equal": True, "excluded_reports_tests_caches": True}))
