#!/usr/bin/env python3
"""Create immutable working copies and a SHA-256 provenance catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


TEXTURES = [
    "Arm_sleeve_Color.png", "Body_Color.png", "Clothes_Color.png",
    "Collapse expression_Color.png", "Eye_1_Color.png", "Eye_Color.png",
    "Face_Color.png", "Hair_1_Color.png", "Hair_2_Color.png", "Latex_Color.png",
]
SVEN_TEXTURES = [
    "Chrome_50Gry1.bmp", "HEV_arm.bmp", "HEV_helmet.bmp",
    "HEV_helmet_top.bmp", "HEV_Lambda_Sign_chrome.bmp",
    "HEV_leg_&_glove.bmp", "HEV_neck.bmp", "HEV_torso.bmp",
    "PLAYER_Chrome1.bmp", "rubbergloveCHROME.bmp", "visor_chrome.bmp",
]
REFERENCES = ["HEV_male_torso_reference.smd", "HEV_male_legs_reference.smd"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_named(root: Path, name: str) -> Path:
    exact = [path for path in root.rglob(name) if path.is_file()]
    if exact:
        return sorted(exact, key=lambda p: (len(p.parts), str(p)))[0]
    stem, suffix = Path(name).stem, Path(name).suffix
    candidates = [
        path for path in root.rglob(f"{stem}*{suffix}")
        if path.is_file() and path.stem.removesuffix(" (1)") == stem
    ]
    if not candidates:
        raise FileNotFoundError(f"Asset ausente: {name} em {root}")
    return sorted(candidates, key=lambda p: (len(p.parts), str(p)))[0]


def safe_copy(source: Path, destination: Path, project: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(source)
    if destination.exists():
        if sha256(destination) != source_hash:
            raise RuntimeError(f"Copia existente diverge; recusando sobrescrever: {destination}")
    else:
        shutil.copy2(source, destination)
    return {
        "source": str(source.resolve()),
        "working_copy": str(destination.relative_to(project)),
        "bytes": source.stat().st_size,
        "sha256": source_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source_root, project = args.source_root.resolve(), args.project_root.resolve()
    arabella_root = source_root / "PlayerModel Arabella"
    sven_root = source_root / "PlayerModel Sven"
    decompiled = sven_root / "decompiled 0.74"
    records: list[dict] = []

    records.append(safe_copy(find_named(arabella_root, "Arabella.pmx"), project / "assets/source/Arabella.pmx", project))
    for name in TEXTURES:
        records.append(safe_copy(find_named(arabella_root, name), project / "assets/source/textures" / name, project))
    records.append(safe_copy(find_named(decompiled, "player.qc"), project / "assets/sven/player.qc", project))
    for name in REFERENCES:
        records.append(safe_copy(find_named(decompiled, name), project / "assets/sven/reference" / name, project))
    for name in SVEN_TEXTURES:
        records.append(safe_copy(find_named(decompiled, name), project / "assets/sven/textures" / name, project))

    archive_candidates = list(sven_root.rglob("player_anims.zip"))
    anim_destination = project / "assets/sven/player_anims"
    anim_destination.mkdir(parents=True, exist_ok=True)
    if archive_candidates:
        archive = archive_candidates[0]
        with zipfile.ZipFile(archive) as bundle:
            members = [m for m in bundle.infolist() if not m.is_dir() and Path(m.filename).suffix.lower() == ".smd"]
            for member in members:
                target = anim_destination / Path(member.filename).name.removesuffix(" (1).smd")
                data = bundle.read(member)
                if target.exists() and target.read_bytes() != data:
                    raise RuntimeError(f"Copia existente diverge; recusando sobrescrever: {target}")
                if not target.exists():
                    target.write_bytes(data)
                records.append({
                    "source": f"{archive.resolve()}::{member.filename}",
                    "working_copy": str(target.relative_to(project)),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                })
        animation_source = "zip"
    else:
        source_anims = decompiled / "player_anims"
        for source in sorted(source_anims.glob("*.smd")):
            normalized = source.name.replace(" (1).smd", ".smd")
            records.append(safe_copy(source, anim_destination / normalized, project))
        animation_source = "already_extracted_decompile_directory"

    report_dir = project / "build/reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "source_root": str(source_root),
        "animation_source": animation_source,
        "files": records,
        "file_count": len(records),
        "all_copies_match_sources": True,
    }
    (report_dir / "asset_catalog.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"files": len(records), "animation_source": animation_source}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
