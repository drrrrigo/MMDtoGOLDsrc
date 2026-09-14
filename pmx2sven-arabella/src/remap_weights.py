#!/usr/bin/env python3
"""Phase 5: semantic MMD-to-Bip01 soft-weight remapping."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_scene import render_previews, world_bbox


def parse_args():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args(values)


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def weighted_pair(a, b, t):
    t = clamp(t)
    return [(a, 1.0 - t), (b, t)]


def semantic_targets(name, z):
    if name in {"操作中心", "全ての親", "センター", "グルーブ"}:
        return [("Bip01", 1.0)]
    if name in {"腰", "下半身", "腰キャンセル左", "腰キャンセル右"}:
        return [("Bip01 Pelvis", 1.0)]
    if name == "上半身":
        return weighted_pair("Bip01 Spine", "Bip01 Spine1", (z - 41.0) / 9.0)
    if name.startswith("上半身2"):
        return weighted_pair("Bip01 Spine2", "Bip01 Spine3", (z - 51.0) / 10.0)
    if name == "首":
        return [("Bip01 Neck", 1.0)]
    if name == "頭" or name in {"右目", "右目戻", "左目", "左目戻", "両目"} or name.startswith("H_") or name.startswith("耳_") or name.startswith("頭"):
        return [("Bip01 Head", 1.0)]
    if name.startswith("Q_"):
        return weighted_pair("Bip01 Spine2", "Bip01 Spine3", (z - 51.0) / 10.0)
    if "胸" in name:
        return [("Bip01 Spine3", 1.0)]

    for jp, side in (("左", "L"), ("右", "R")):
        if name.startswith(jp + "肩"):
            return [(f"Bip01 {side} Arm", 1.0)]
        if name.startswith(jp + "腕"):
            return [(f"Bip01 {side} Arm1", 1.0)]
        if name.startswith(jp + "ひじ") or name.startswith(jp + "手捩"):
            return [(f"Bip01 {side} Arm2", 1.0)]
        if name.startswith(jp + "手首") or name.startswith(jp + "手先") or name.startswith(jp + "ダミー"):
            return [(f"Bip01 {side} Hand", 1.0)]
        finger = {
            "親指０": "Finger0", "親指１": "Finger01", "親指２": "Finger02", "親指先": "Finger02",
            "人指１": "Finger1", "人指２": "Finger11", "人指３": "Finger12", "人指先": "Finger12",
        }
        for token, target in finger.items():
            if name.startswith(jp + token):
                return [(f"Bip01 {side} {target}", 1.0)]
        if any(name.startswith(jp + token) for token in ("中指", "薬指", "小指")):
            return [(f"Bip01 {side} Hand", 1.0)]

    leg_rules = {
        "左足D": "Bip01 L Leg", "左ひざD": "Bip01 L Leg1", "左足首D": "Bip01 L Foot", "左足先EX": "Bip01 L Foot",
        "右足D": "Bip01 R Leg", "右ひざD": "Bip01 R Leg1", "右足首D": "Bip01 R Foot", "右足先EX": "Bip01 R Foot",
        "左足": "Bip01 L Leg", "左ひざ": "Bip01 L Leg1", "左足首": "Bip01 L Foot", "左つま先": "Bip01 L Foot",
        "右足": "Bip01 R Leg", "右ひざ": "Bip01 R Leg1", "右足首": "Bip01 R Foot", "右つま先": "Bip01 R Foot",
    }
    if name in leg_rules:
        return [(leg_rules[name], 1.0)]
    if name.startswith("リボン"):
        side = "L" if name.endswith("_L") else "R" if name.endswith("_R") else None
        if side:
            return [(f"Bip01 {side} Arm1" if z > 52.0 else f"Bip01 {side} Arm2", 1.0)]
    return []


def spatial_fallback(co):
    x, _y, z = co
    side = "L" if x >= 0 else "R"
    if z < 9:
        return [(f"Bip01 {side} Foot", 1.0)]
    if z < 30:
        return [(f"Bip01 {side} Leg1", 1.0)]
    if z < 43:
        return [(f"Bip01 {side} Leg", 1.0)]
    if z < 48:
        return [("Bip01 Pelvis", 1.0)]
    if z < 52:
        return [("Bip01 Spine", 1.0)]
    if z < 57:
        return [("Bip01 Spine2", 1.0)]
    if z < 63:
        return [("Bip01 Spine3", 1.0)]
    if z < 65:
        return [("Bip01 Neck", 1.0)]
    return [("Bip01 Head", 1.0)]


def apply_world_transform(obj):
    world = obj.matrix_world.copy()
    obj.parent = None
    obj.matrix_world = world
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def remap_mesh(obj, bip, target_names):
    apply_world_transform(obj)
    source_names = {group.index: group.name for group in obj.vertex_groups}
    assignments = []
    stats = Counter()
    for vertex in obj.data.vertices:
        aggregate = defaultdict(float)
        for membership in vertex.groups:
            source_name = source_names.get(membership.group, "")
            targets = semantic_targets(source_name, vertex.co.z)
            for target, factor in targets:
                aggregate[target] += membership.weight * factor
        if not aggregate or sum(aggregate.values()) <= 1e-8:
            stats["fallback_vertices"] += 1
            for target, factor in spatial_fallback(vertex.co):
                aggregate[target] += factor
        total = sum(aggregate.values())
        normalized = [(target, weight / total) for target, weight in aggregate.items() if target in target_names and weight > 1e-6]
        if not normalized:
            normalized = spatial_fallback(vertex.co)
            stats["forced_fallback_vertices"] += 1
        assignments.append(normalized)
        stats[f"influences_{len(normalized)}"] += 1

    obj.vertex_groups.clear()
    groups = {name: obj.vertex_groups.new(name=name) for name in target_names}
    for vertex, weights in zip(obj.data.vertices, assignments):
        for target, weight in weights:
            groups[target].add([vertex.index], weight, "REPLACE")
    for modifier in list(obj.modifiers):
        if modifier.type == "ARMATURE":
            obj.modifiers.remove(modifier)
    modifier = obj.modifiers.new("Bip01_SoftWeights_PHASE5", "ARMATURE")
    modifier.object = bip
    return dict(stats)


def validate_mesh(obj, target_names):
    group_names = {group.name for group in obj.vertex_groups}
    unweighted = 0
    min_influences = 999
    max_influences = 0
    nonfinite = 0
    for vertex in obj.data.vertices:
        influences = [membership for membership in vertex.groups if membership.weight > 1e-8]
        if not influences:
            unweighted += 1
        min_influences = min(min_influences, len(influences))
        max_influences = max(max_influences, len(influences))
        if not all(math.isfinite(membership.weight) for membership in influences):
            nonfinite += 1
    return {"vertices": len(obj.data.vertices), "triangles": len(obj.data.polygons), "unweighted_vertices": unweighted, "min_influences": min_influences if len(obj.data.vertices) else 0, "max_influences": max_influences, "nonfinite_weights": nonfinite, "groups_exactly_bip01": group_names == set(target_names), "groups": sorted(group_names)}


def remove_preview_setup():
    for obj in list(bpy.data.objects):
        if obj.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def main():
    args = parse_args()
    project = args.project_root.resolve()
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_05_before_weight_remap.blend"), check_existing=False)
    collection = bpy.data.collections["Arabella_Result"]
    meshes = [obj for obj in collection.all_objects if obj.type == "MESH"]
    bip = bpy.data.objects["Bip01"]
    target_names = [bone.name for bone in bip.data.bones]
    if len(target_names) != 34:
        raise RuntimeError(f"Bip01 alterado: esperado 34 bones, encontrados {len(target_names)}")
    remap_stats = {obj.name: remap_mesh(obj, bip, target_names) for obj in meshes}

    for obj in list(collection.all_objects):
        if obj.type in {"ARMATURE", "EMPTY"} and obj.name != "Bip01":
            bpy.data.objects.remove(obj, do_unlink=True)
    validations = {obj.name: validate_mesh(obj, target_names) for obj in meshes}

    bip.data.pose_position = "POSE"
    bip.animation_data_create()
    wave_action = bpy.data.actions.get("action_wave")
    if wave_action is None:
        bpy.ops.object.select_all(action="DESELECT")
        bip.select_set(True)
        bpy.context.view_layer.objects.active = bip
        before_actions = set(bpy.data.actions)
        result = bpy.ops.import_scene.smd(
            filepath=str(project / "assets/sven/player_anims/action_wave.smd"),
            doAnim=True, createCollections=False, makeCamera=False,
            append="APPEND", upAxis="Z", rotMode="XYZ", boneMode="NONE",
        )
        if "FINISHED" not in result:
            raise RuntimeError(f"Falha ao reimportar action_wave: {result}")
        created_actions = list(set(bpy.data.actions) - before_actions)
        wave_action = created_actions[0] if created_actions else bpy.data.actions.get("action_wave")
        if wave_action is None:
            raise RuntimeError("Source Tools nao criou a action action_wave")
        wave_action.name = "action_wave"
    wave_action.use_fake_user = True
    bip.animation_data.action = wave_action
    bpy.context.scene.frame_set(10)
    bpy.context.view_layer.update()
    reference_meshes = [obj for obj in bpy.data.collections["Sven_Reference"].all_objects if obj.type == "MESH"]
    remove_preview_setup()
    generated = render_previews(project, meshes + reference_meshes)
    previews = []
    for relative in generated:
        source = project / relative
        target = source.with_name(source.name.replace("base_", "phase5_wave_f10_"))
        source.replace(target)
        previews.append(str(target.relative_to(project)))
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_05_wave_test.blend"), check_existing=False)

    bip.animation_data.action = None
    bip.data.pose_position = "REST"
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    passed = all(item["unweighted_vertices"] == 0 and item["nonfinite_weights"] == 0 and item["groups_exactly_bip01"] for item in validations.values())
    report = {
        "phase": 5,
        "mapping_policy": "semantic source weights plus vertical spine distribution and spatial fallback",
        "bip01_bones": target_names,
        "bip01_bind_pose_modified": False,
        "soft_weights_retained": True,
        "remap_stats": remap_stats,
        "validation": validations,
        "total_vertices": sum(item["vertices"] for item in validations.values()),
        "total_triangles": sum(item["triangles"] for item in validations.values()),
        "unweighted_vertices": sum(item["unweighted_vertices"] for item in validations.values()),
        "nonfinite_weights": sum(item["nonfinite_weights"] for item in validations.values()),
        "previews_action_wave_frame_10": previews,
        "rest_bbox": world_bbox(meshes),
        "passed": passed,
    }
    (project / "build/reports/phase5_weights.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_05_soft_weights.blend"), check_existing=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/arabella_work.blend"), check_existing=False)
    print(json.dumps({"phase": 5, "passed": passed, "vertices": report["total_vertices"], "triangles": report["total_triangles"], "unweighted": report["unweighted_vertices"], "previews": previews}))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
