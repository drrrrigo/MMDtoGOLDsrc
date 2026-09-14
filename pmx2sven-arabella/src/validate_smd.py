#!/usr/bin/env python3
"""Validate QC animation references and SMD skeleton invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


NODE_RE = re.compile(r'^\s*(-?\d+)\s+"(.*)"\s+(-?\d+)\s*$')
TIME_RE = re.compile(r"^\s*time\s+(-?\d+)\s*$", re.IGNORECASE)
SEQUENCE_RE = re.compile(r"^\s*\$sequence\b", re.IGNORECASE | re.MULTILINE)
ANIMATION_REF_RE = re.compile(r'"(player_anims[\\/][^"\r\n]+)"', re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_smd(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    section = None
    nodes: list[tuple[int, str, int]] = []
    times: list[int] = []
    triangle_blocks = 0
    triangle_lines = 0
    for line in text.splitlines():
        token = line.strip().lower()
        if token in {"nodes", "skeleton", "triangles", "vertexanimation"}:
            section = token
            continue
        if token == "end":
            section = None
            continue
        if section == "nodes":
            match = NODE_RE.match(line)
            if match:
                nodes.append((int(match.group(1)), match.group(2), int(match.group(3))))
        elif section == "skeleton":
            match = TIME_RE.match(line)
            if match:
                times.append(int(match.group(1)))
        elif section == "triangles" and token:
            if triangle_lines % 4 == 0:
                triangle_blocks += 1
            triangle_lines += 1
    signature_payload = json.dumps(nodes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    duplicate_ids = sorted(key for key, value in Counter(n[0] for n in nodes).items() if value > 1)
    duplicate_names = sorted(key for key, value in Counter(n[1] for n in nodes).items() if value > 1)
    valid_parent_ids = {node[0] for node in nodes}
    invalid_parents = [{"bone": name, "parent": parent} for _, name, parent in nodes if parent != -1 and parent not in valid_parent_ids]
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "bones": len(nodes),
        "nodes": [{"id": number, "name": name, "parent": parent} for number, name, parent in nodes],
        "skeleton_signature": hashlib.sha256(signature_payload).hexdigest(),
        "frame_count": len(set(times)),
        "frame_min": min(times) if times else None,
        "frame_max": max(times) if times else None,
        "triangles": triangle_blocks,
        "duplicate_bone_ids": duplicate_ids,
        "duplicate_bone_names": duplicate_names,
        "invalid_parents": invalid_parents,
    }


def normalize_ref(value: str) -> str:
    value = value.replace("\\", "/")
    name = Path(value).name
    return name if name.lower().endswith(".smd") else name + ".smd"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qc", type=Path, required=True)
    parser.add_argument("--animations", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    qc_text = args.qc.read_text(encoding="utf-8-sig", errors="replace")
    qc_refs_raw = ANIMATION_REF_RE.findall(qc_text)
    qc_files = [normalize_ref(value) for value in qc_refs_raw]
    qc_lookup = {name.casefold(): name for name in qc_files}
    animation_paths = sorted(args.animations.glob("*.smd"), key=lambda p: p.name.casefold())
    disk_lookup = {path.name.casefold(): path.name for path in animation_paths}
    missing = sorted(qc_lookup[key] for key in qc_lookup.keys() - disk_lookup.keys())
    unexpected = sorted(disk_lookup[key] for key in disk_lookup.keys() - qc_lookup.keys())
    duplicate_refs = sorted(name for name, count in Counter(name.casefold() for name in qc_files).items() if count > 1)

    animation_reports = [parse_smd(path) for path in animation_paths]
    reference_reports = [parse_smd(path) for path in sorted(args.references.glob("*.smd"))]
    signatures: dict[str, list[str]] = defaultdict(list)
    for report in animation_reports:
        signatures[report["skeleton_signature"]].append(report["name"])
    bones_per_animation = sorted(set(report["bones"] for report in animation_reports))
    sequence_count = len(SEQUENCE_RE.findall(qc_text))
    checks = {
        "qc_animation_references_349": len(qc_refs_raw) == 349,
        "animation_smd_files_349": len(animation_paths) == 349,
        "missing_animations_0": len(missing) == 0,
        "unexpected_animations_0": len(unexpected) == 0,
        "duplicate_references_0": len(duplicate_refs) == 0,
        "skeleton_variants_1": len(signatures) == 1,
        "bones_per_animation_34": bones_per_animation == [34],
        "sequences_191": sequence_count == 191,
        "references_match_animation_skeleton": bool(reference_reports) and all(
            report["skeleton_signature"] in signatures for report in reference_reports
        ),
    }
    report = {
        "qc": {"path": str(args.qc.resolve()), "sha256": sha256(args.qc), "sequences": sequence_count, "animation_references": len(qc_refs_raw), "references": qc_files},
        "animations": {
            "directory": str(args.animations.resolve()), "files": len(animation_paths),
            "missing": missing, "unexpected": unexpected, "duplicate_qc_references": duplicate_refs,
            "skeleton_variants": len(signatures), "bones_per_animation": bones_per_animation,
            "signature_members": dict(signatures), "details": animation_reports,
        },
        "reference_meshes": reference_reports,
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "sequences": sequence_count, "qc_references": len(qc_refs_raw), "animation_files": len(animation_paths),
        "missing": len(missing), "unexpected": len(unexpected), "skeleton_variants": len(signatures),
        "bones_per_animation": bones_per_animation, "passed": report["passed"],
    }))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
