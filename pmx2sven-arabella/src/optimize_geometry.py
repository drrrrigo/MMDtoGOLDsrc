#!/usr/bin/env python3
"""Phase 4: split by material and decimate each region independently."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_scene import render_previews, world_bbox


TARGETS = {
    "Eye": 230, "Eye_1": 170, "Body": 3200, "Arm sleeve": 1100,
    "Latex_1": 200, "Collapse expression": 1564, "Face": 1100,
    "Latex": 1000, "Clothes": 700, "Hair_2.001": 550, "Hair_1": 1600,
}


def parse_args():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args(values)


def material_name(obj):
    names = [slot.material.name for slot in obj.material_slots if slot.material]
    if not names:
        return "UNMATERIALIZED"
    name = names[0].removeprefix("ArabellaPreview_")
    for candidate in sorted(TARGETS, key=len, reverse=True):
        clean = re.sub(r"\.\d{3}$", "", name)
        if clean == re.sub(r"\.\d{3}$", "", candidate):
            return candidate
    return name


def topology_metrics(obj):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    degenerate = sum(1 for face in bm.faces if face.calc_area() <= 1e-12)
    non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
    bm.free()
    finite_vertices = all(math.isfinite(value) for vertex in mesh.vertices for value in vertex.co)
    finite_normals = all(math.isfinite(value) for polygon in mesh.polygons for value in polygon.normal)
    finite_uv = all(math.isfinite(value) for layer in mesh.uv_layers for loop in layer.data for value in loop.uv)
    return {"vertices": len(mesh.vertices), "triangles": len(mesh.polygons), "degenerate_faces": degenerate, "non_manifold_edges": non_manifold, "finite_vertices": finite_vertices, "finite_normals": finite_normals, "finite_uv": finite_uv}


def remove_degenerate_faces(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bad = [face for face in bm.faces if face.calc_area() <= 1e-12]
    count = len(bad)
    if bad:
        bmesh.ops.delete(bm, geom=bad, context="FACES")
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return count


def remove_preview_setup():
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def main():
    args = parse_args()
    project = args.project_root.resolve()
    collection = bpy.data.collections["Arabella_Result"]
    source_meshes = [obj for obj in collection.all_objects if obj.type == "MESH"]
    if len(source_meshes) != 1:
        raise RuntimeError(f"Fase 4 esperava uma mesh antes da separacao; encontrou {len(source_meshes)}")
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_04_before_optimization.blend"), check_existing=False)

    source = source_meshes[0]
    bpy.ops.object.select_all(action="DESELECT")
    source.hide_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="MATERIAL")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    parts = [obj for obj in collection.all_objects if obj.type == "MESH"]

    results = []
    for obj in parts:
        material = material_name(obj)
        before = topology_metrics(obj)
        target = TARGETS.get(material, before["triangles"])
        protected = material == "Collapse expression"
        if before["triangles"] > target and not protected:
            modifier = obj.modifiers.new("Phase4_Incremental_Decimate", "DECIMATE")
            modifier.decimate_type = "COLLAPSE"
            modifier.ratio = max(0.01, min(1.0, target / before["triangles"]))
            modifier.use_collapse_triangulate = True
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        degenerate_removed = remove_degenerate_faces(obj)
        obj.data.validate(clean_customdata=False)
        obj.name = "Arabella_" + re.sub(r"[^A-Za-z0-9_]+", "_", material).strip("_")
        after = topology_metrics(obj)
        results.append({"material": material, "target": target, "protected": protected, "degenerate_removed": degenerate_removed, "before": before, "after": after})

    total_before = sum(item["before"]["triangles"] for item in results)
    total_after = sum(item["after"]["triangles"] for item in results)
    work_meshes = [obj for obj in collection.all_objects if obj.type == "MESH"]
    reference_meshes = [obj for obj in bpy.data.collections["Sven_Reference"].all_objects if obj.type == "MESH"]
    remove_preview_setup()
    generated = render_previews(project, work_meshes + reference_meshes)
    previews = []
    for relative in generated:
        source_path = project / relative
        target_path = source_path.with_name(source_path.name.replace("base_", "phase4_optimized_"))
        source_path.replace(target_path)
        previews.append(str(target_path.relative_to(project)))

    report = {
        "phase": 4,
        "strategy": "material-separated incremental decimation",
        "total_before": total_before,
        "total_after": total_after,
        "target_range": [8000, 12000],
        "within_target": 8000 <= total_after <= 12000,
        "collapse_expression_retained": True,
        "parts": sorted(results, key=lambda item: item["material"]),
        "bbox": world_bbox(work_meshes),
        "previews": previews,
        "all_finite": all(item["after"]["finite_vertices"] and item["after"]["finite_normals"] and item["after"]["finite_uv"] for item in results),
        "degenerate_faces": sum(item["after"]["degenerate_faces"] for item in results),
        "non_manifold_edges": sum(item["after"]["non_manifold_edges"] for item in results),
    }
    (project / "build/reports/phase4_geometry.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_04_optimized.blend"), check_existing=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/arabella_work.blend"), check_existing=False)
    print(json.dumps({"phase": 4, "before": total_before, "after": total_after, "within_target": report["within_target"], "previews": previews}))
    return 0 if report["within_target"] and report["all_finite"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
