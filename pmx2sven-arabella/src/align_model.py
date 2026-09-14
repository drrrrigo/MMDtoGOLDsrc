#!/usr/bin/env python3
"""Phase 3: conform Arabella's arm pose to Sven vectors, then bake safely."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_scene import render_previews, world_bbox


def parse_args():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args(values)


def world_head(armature, bone_name):
    return armature.matrix_world @ armature.data.bones[bone_name].head_local


def pose_head_world(armature, bone_name):
    return armature.matrix_world @ armature.pose.bones[bone_name].head


def pose_tail_world(armature, bone_name):
    return armature.matrix_world @ armature.pose.bones[bone_name].tail


def point_pose_bone(armature, bone_name, desired_world_direction):
    pb = armature.pose.bones[bone_name]
    desired_local = (armature.matrix_world.inverted().to_3x3() @ Vector(desired_world_direction)).normalized()
    current = pb.vector.normalized()
    rotation = current.rotation_difference(desired_local)
    head = pb.head.copy()
    pb.matrix = Matrix.Translation(head) @ rotation.to_matrix().to_4x4() @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()


def remove_old_preview_setup():
    for obj in list(bpy.data.objects):
        if obj.name.startswith("Phase2_Preview_Camera") or obj.name.startswith("Phase2_Key") or obj.name.startswith("Phase2_Fill"):
            bpy.data.objects.remove(obj, do_unlink=True)


def main():
    args = parse_args()
    project = args.project_root.resolve()
    work_collection = bpy.data.collections["Arabella_Result"]
    reference_collection = bpy.data.collections["Sven_Reference"]
    mmd = bpy.data.objects["Arabella_arm.001"]
    bip = bpy.data.objects["Bip01"]
    bip.data.pose_position = "REST"
    if bip.animation_data:
        bip.animation_data.action = None
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()

    checkpoint = project / "blender/checkpoints/phase_03_before_pose_bake.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint), check_existing=False)

    pairs = {
        "L": {"upper": "左腕", "fore": "左ひじ", "sven_upper": "Bip01 L Arm1", "sven_elbow": "Bip01 L Arm2", "sven_hand": "Bip01 L Hand"},
        "R": {"upper": "右腕", "fore": "右ひじ", "sven_upper": "Bip01 R Arm1", "sven_elbow": "Bip01 R Arm2", "sven_hand": "Bip01 R Hand"},
    }
    measurements = {}
    for side, names in pairs.items():
        before = {
            "upper_head": list(pose_head_world(mmd, names["upper"])),
            "elbow_head": list(pose_head_world(mmd, names["fore"])),
            "wrist": list(pose_tail_world(mmd, names["fore"])),
        }
        upper_direction = world_head(bip, names["sven_elbow"]) - world_head(bip, names["sven_upper"])
        fore_direction = world_head(bip, names["sven_hand"]) - world_head(bip, names["sven_elbow"])
        point_pose_bone(mmd, names["upper"], upper_direction)
        point_pose_bone(mmd, names["fore"], fore_direction)
        after = {
            "upper_head": list(pose_head_world(mmd, names["upper"])),
            "elbow_head": list(pose_head_world(mmd, names["fore"])),
            "wrist": list(pose_tail_world(mmd, names["fore"])),
        }
        measurements[side] = {"before": before, "after_preview_pose": after, "target_hand": list(world_head(bip, names["sven_hand"]))}

    work_meshes = [obj for obj in work_collection.all_objects if obj.type == "MESH"]
    reference_meshes = [obj for obj in reference_collection.all_objects if obj.type == "MESH"]
    remove_old_preview_setup()
    preview_paths = render_previews(project, work_meshes + reference_meshes)
    renamed_previews = []
    for relative in preview_paths:
        source = project / relative
        target = source.with_name(source.name.replace("base_", "phase3_pose_"))
        source.replace(target)
        renamed_previews.append(str(target.relative_to(project)))
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_03_pose_preview.blend"), check_existing=False)

    applied_modifiers = []
    for mesh in work_meshes:
        armature_modifiers = [mod for mod in mesh.modifiers if mod.type == "ARMATURE" and mod.object == mmd]
        for modifier in armature_modifiers:
            bpy.ops.object.select_all(action="DESELECT")
            mesh.hide_set(False)
            mesh.select_set(True)
            bpy.context.view_layer.objects.active = mesh
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            applied_modifiers.append({"object": mesh.name, "modifier": modifier.name})
    for pb in mmd.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()

    report = {
        "phase": 3,
        "orientation": {"arabella_front": "-Y", "sven_front": "-Y", "left_side_x": "positive", "right_side_x": "negative", "corrected": True},
        "scale": [3.48, 3.48, 3.48],
        "measurements": measurements,
        "work_bbox": world_bbox(work_meshes),
        "reference_bbox": world_bbox(reference_meshes),
        "preview_before_bake": renamed_previews,
        "baked_armature_modifiers": applied_modifiers,
        "bip01_bind_pose_modified": False,
    }
    output = project / "build/reports/phase3_alignment.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/checkpoints/phase_03_aligned.blend"), check_existing=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/arabella_work.blend"), check_existing=False)
    print(json.dumps({"phase": 3, "previews": renamed_previews, "applied_modifiers": applied_modifiers}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
