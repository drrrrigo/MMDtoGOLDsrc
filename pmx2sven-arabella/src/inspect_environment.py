#!/usr/bin/env python3
"""Run with Blender to inventory versions, add-ons and real bpy operators."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path

import bpy


def operator_info(operator):
    rna = operator.get_rna_type()
    return {"identifier": rna.identifier, "properties": [prop.identifier for prop in rna.properties]}


def main() -> int:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    project = Path(values[values.index("--project-root") + 1]).resolve() if "--project-root" in values else Path.cwd()
    search_roots = [Path("/home/rodrigo/.local/share/Steam"), Path("/home/rodrigo/.steam"), Path("/mnt/data"), Path("/opt")]
    compilers = []
    for root in search_roots:
        if root.exists():
            for pattern in ("studiomdl", "studiomdl.exe"):
                compilers.extend(str(path.resolve()) for path in root.rglob(pattern) if path.is_file())
    addons = sorted(bpy.context.preferences.addons.keys())
    mmd_id = next((name for name in addons if name.endswith("mmd_tools")), None)
    source_id = next((name for name in addons if name.endswith("io_scene_valvesource")), None)
    report = {
        "platform": platform.platform(),
        "blender": {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "version_tuple": list(bpy.app.version)},
        "python": {"executable": sys.executable, "version": platform.python_version()},
        "wine": shutil.which("wine"),
        "studiomdl_candidates": sorted(set(compilers)),
        "addons": {"mmd_tools": {"enabled": mmd_id is not None, "identifier": mmd_id}, "source_tools": {"enabled": source_id is not None, "identifier": source_id}},
        "operators": {
            "mmd_import": operator_info(bpy.ops.mmd_tools.import_model),
            "smd_import": operator_info(bpy.ops.import_scene.smd),
            "smd_export": operator_info(bpy.ops.export_scene.smd),
        },
        "compatibility_notes": [
            "Ambiente esperado citava Blender 4.x; Blender 5.2.1 foi validado por importacao real.",
            "APIs de constraints e identificador Eevee foram detectados/adaptados no script.",
        ],
        "missing_dependencies": ["Sven Co-op studiomdl"] if not compilers else [],
    }
    output = project / "build/reports/environment.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"blender": bpy.app.version_string, "mmd_tools": mmd_id, "source_tools": source_id, "studiomdl": compilers}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
