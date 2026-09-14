#!/usr/bin/env python3
"""Validate the saved phase-2 Blender scene without modifying its contents."""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

import bpy


def main() -> int:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--phase", type=int, default=2)
    args = parser.parse_args(values)
    project = args.project_root.resolve()
    expected = json.loads((project / "build/reports/sven_validation.json").read_text(encoding="utf-8"))
    expected_nodes = expected["animations"]["details"][0]["nodes"]
    expected_names = [node["name"] for node in expected_nodes]
    required_collections = {"Arabella_Original", "Arabella_Result", "Sven_Reference", "Scene_Setup"}
    original = bpy.data.collections.get("Arabella_Original")
    working = bpy.data.collections.get("Arabella_Result")
    bip01_objects = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and obj.name == "Bip01"]
    work_meshes = [obj for obj in working.all_objects if obj.type == "MESH"] if working else []
    original_meshes = [obj for obj in original.all_objects if obj.type == "MESH" and str(getattr(obj, "mmd_type", "NONE")) != "RIGID_BODY"] if original else []
    original_shape_keys = sum(max(0, len(obj.data.shape_keys.key_blocks) - 1) for obj in original_meshes if obj.data.shape_keys)
    bone_names = [bone.name for bone in bip01_objects[0].data.bones] if len(bip01_objects) == 1 else []
    preview_prefix = "phase5_wave_f10" if args.phase >= 5 else "base"
    preview_paths = [project / "build/previews" / f"{preview_prefix}_{view}.png" for view in ("front", "side", "perspective")]
    work_vertices = sum(len(obj.data.vertices) for obj in work_meshes)
    work_triangles = sum(len(obj.data.polygons) for obj in work_meshes)
    original_vertices = sum(len(obj.data.vertices) for obj in original_meshes)
    original_triangles = sum(len(obj.data.polygons) for obj in original_meshes)
    if args.phase >= 5:
        phase5 = json.loads((project / "build/reports/phase5_weights.json").read_text(encoding="utf-8"))
        expected_work_vertices = phase5["total_vertices"]
        expected_work_triangles = phase5["total_triangles"]
    else:
        expected_work_vertices, expected_work_triangles = 67729, 81586
    target_groups = set(expected_names)
    weights_valid = all(
        {group.name for group in obj.vertex_groups} == target_groups
        and all(any(membership.weight > 1e-8 for membership in vertex.groups) for vertex in obj.data.vertices)
        for obj in work_meshes
    ) if args.phase >= 5 else True
    modifiers_valid = all(any(mod.type == "ARMATURE" and mod.object == bip01_objects[0] for mod in obj.modifiers) for obj in work_meshes) if args.phase >= 5 and len(bip01_objects) == 1 else args.phase < 5
    checks = {
        "required_collections": required_collections.issubset(bpy.data.collections.keys()),
        "original_hidden": bool(original and original.hide_viewport and original.hide_render),
        "single_bip01_armature": len(bip01_objects) == 1,
        "bip01_has_34_bones": len(bone_names) == 34,
        "bip01_names_and_order_match": bone_names == expected_names,
        "action_wave_loaded": "action_wave" in bpy.data.actions,
        "working_geometry_expected_for_phase": work_vertices == expected_work_vertices and work_triangles == expected_work_triangles,
        "original_geometry_preserved": original_vertices == 67729 and original_triangles == 81586,
        "original_morphs_preserved": original_shape_keys == 2,
        "working_morphs_removed": all(obj.data.shape_keys is None for obj in work_meshes),
        "working_constraints_removed": all(len(obj.constraints) == 0 for obj in working.all_objects) if working else False,
        "phase5_weights_valid": weights_valid,
        "phase5_armature_modifiers_valid": modifiers_valid,
        "preview_parent_not_applied": "Arabella_Alignment_PREVIEW_NOT_APPLIED" in bpy.data.objects,
        "previews_exist": all(path.exists() and path.stat().st_size > 0 for path in preview_paths),
    }
    report = {
        "blend_file": bpy.data.filepath,
        "blender_version": bpy.app.version_string,
        "phase": args.phase,
        "metrics": {"work_vertices": work_vertices, "work_triangles": work_triangles, "original_vertices": original_vertices, "original_triangles": original_triangles, "original_morphs": original_shape_keys, "bip01_bones": len(bone_names)},
        "checks": checks,
        "passed": all(checks.values()),
    }
    output = project / f"build/reports/phase{args.phase}_validation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
