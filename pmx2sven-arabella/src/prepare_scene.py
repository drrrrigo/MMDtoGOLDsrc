#!/usr/bin/env python3
"""Build the non-destructive Blender checkpoint for phases 1 and 2."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def args_after_separator() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    return parser.parse_args(values)


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if collection.name not in {item.name for item in bpy.context.scene.collection.children}:
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(objects, collection):
    for obj in objects:
        for current in list(obj.users_collection):
            current.objects.unlink(obj)
        collection.objects.link(obj)


def import_pmx(path: Path, collection: bpy.types.Collection):
    before = set(bpy.data.objects)
    result = bpy.ops.mmd_tools.import_model(
        filepath=str(path), types={"MESH", "ARMATURE", "PHYSICS", "DISPLAY", "MORPHS"},
        scale=1.0, clean_model=False, remove_doubles=False,
        fix_bone_order=False, fix_ik_links=False, apply_bone_fixed_axis=False,
        rename_bones=False, use_mipmap=True, log_level="WARNING", save_log=False,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Falha ao importar PMX: {result}")
    imported = list(set(bpy.data.objects) - before)
    move_to_collection(imported, collection)
    return imported


def selected_armature(objects):
    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"Esperado um armature Sven; encontrados {len(armatures)}")
    bpy.ops.object.select_all(action="DESELECT")
    armatures[0].select_set(True)
    bpy.context.view_layer.objects.active = armatures[0]
    return armatures[0]


def import_smd(path: Path, collection, append: str, do_anim: bool, target=None):
    if target:
        bpy.ops.object.select_all(action="DESELECT")
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
    before = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    result = bpy.ops.import_scene.smd(
        filepath=str(path), doAnim=do_anim, createCollections=False,
        makeCamera=False, append=append, upAxis="Z", rotMode="XYZ", boneMode="NONE",
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Falha ao importar SMD {path.name}: {result}")
    imported = list(set(bpy.data.objects) - before)
    move_to_collection(imported, collection)
    return imported, list(set(bpy.data.actions) - before_actions)


def cleanup_working_copy(objects) -> dict:
    removed = []
    constraints = 0
    pose_constraints = 0
    morph_objects = []
    morph_metadata_removed = []
    physics_types = {"RIGID_BODY", "JOINT", "RIGID_GRP_OBJ", "JOINT_GRP_OBJ"}
    for obj in list(objects):
        mmd_type = str(getattr(obj, "mmd_type", "NONE"))
        if mmd_type in physics_types or obj.rigid_body is not None or obj.rigid_body_constraint is not None:
            removed.append({"name": obj.name, "type": obj.type, "mmd_type": mmd_type})
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        constraints += len(obj.constraints)
        for constraint in list(obj.constraints):
            obj.constraints.remove(constraint)
        if obj.type == "ARMATURE":
            for bone in obj.pose.bones:
                pose_constraints += len(bone.constraints)
                for constraint in list(bone.constraints):
                    bone.constraints.remove(constraint)
        if obj.type == "MESH" and obj.data.shape_keys:
            morph_objects.append({"object": obj.name, "shape_keys": len(obj.data.shape_keys.key_blocks)})
            for key in list(obj.data.shape_keys.key_blocks)[::-1]:
                obj.shape_key_remove(key)
        if mmd_type == "ROOT":
            for property_name in ("vertex_morphs", "bone_morphs", "material_morphs", "uv_morphs", "group_morphs"):
                collection = getattr(obj.mmd_root, property_name)
                count = len(collection)
                if count:
                    collection.clear()
                    morph_metadata_removed.append({"property": property_name, "count": count})
    if bpy.context.scene.rigidbody_world:
        bpy.ops.rigidbody.world_remove()
    return {
        "removed_objects": removed,
        "object_constraints_removed": constraints,
        "pose_constraints_removed": pose_constraints,
        "morphs_removed_from": morph_objects,
        "morph_metadata_removed": morph_metadata_removed,
    }


def material_key(name: str, candidates) -> str | None:
    def clean(value):
        while value.rsplit(".", 1)[-1].isdigit() and "." in value:
            value = value.rsplit(".", 1)[0]
        return value
    cleaned = clean(name)
    for candidate in sorted(candidates, key=len, reverse=True):
        if cleaned == clean(candidate):
            return candidate
    return None


def create_preview_materials(objects, project: Path, pmx_report: dict) -> list[dict]:
    lookup = {m["name_local"]: m["texture_basename"] for m in pmx_report["materials"]}
    created = {}
    assignments = []
    for obj in objects:
        if obj.type != "MESH" or str(getattr(obj, "mmd_type", "NONE")) == "RIGID_BODY":
            continue
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            key = material_key(slot.material.name, lookup)
            texture_name = lookup.get(key) if key else None
            texture_path = project / "assets/source/textures" / texture_name if texture_name else None
            if not texture_path or not texture_path.exists():
                assignments.append({"object": obj.name, "material": slot.material.name, "texture": None})
                continue
            if key not in created:
                image = bpy.data.images.load(str(texture_path), check_existing=True)
                mat = bpy.data.materials.new(f"ArabellaPreview_{key}")
                mat.use_nodes = True
                nodes = mat.node_tree.nodes
                bsdf = nodes.get("Principled BSDF")
                image_node = nodes.new("ShaderNodeTexImage")
                image_node.image = image
                image_node.interpolation = "Linear"
                mat.node_tree.links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
                mat.node_tree.links.new(image_node.outputs["Alpha"], bsdf.inputs["Alpha"])
                mat.surface_render_method = "DITHERED"
                mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
                created[key] = mat
            slot.material = created[key]
            assignments.append({"object": obj.name, "material": key, "texture": str(texture_path.relative_to(project))})
    return assignments


def style_reference(objects):
    material = bpy.data.materials.new("SvenReference_TransparentBlue")
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.03, 0.25, 0.8, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.65
    bsdf.inputs["Alpha"].default_value = 0.32
    material.surface_render_method = "DITHERED"
    for obj in objects:
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(material)


def world_bbox(objects):
    points = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    if not points:
        return None
    mins = Vector((min(p[i] for p in points) for i in range(3)))
    maxs = Vector((max(p[i] for p in points) for i in range(3)))
    return {"min": list(mins), "max": list(maxs), "size": list(maxs - mins), "center": list((mins + maxs) / 2)}


def point_camera(camera, location, target):
    camera.location = location
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()


def render_previews(project: Path, visible_objects) -> list[str]:
    scene = bpy.context.scene
    render_engines = {item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in render_engines else "BLENDER_WORKBENCH"
    scene.render.resolution_x = 768
    scene.render.resolution_y = 768
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.055, 0.055, 0.055, 1.0)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.45
    bbox = world_bbox(visible_objects)
    if bbox is None:
        raise RuntimeError("Nao ha meshes visiveis para preview")
    center, size = Vector(bbox["center"]), Vector(bbox["size"])
    radius = max(size) * 0.72
    camera_data = bpy.data.cameras.new("Phase2_Preview_Camera")
    camera = bpy.data.objects.new("Phase2_Preview_Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera_data.lens = 55
    scene.camera = camera
    light_data = bpy.data.lights.new("Phase2_Key", "AREA")
    light_data.energy = 80000
    light_data.shape = "DISK"
    light_data.size = max(size) * 0.8
    light = bpy.data.objects.new("Phase2_Key", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = center + Vector((-radius, -radius, radius * 1.5))
    point_camera(light, light.location, center)
    fill_data = bpy.data.lights.new("Phase2_Fill", "AREA")
    fill_data.energy = 30000
    fill_data.size = max(size)
    fill = bpy.data.objects.new("Phase2_Fill", fill_data)
    bpy.context.scene.collection.objects.link(fill)
    fill.location = center + Vector((radius, radius * 0.5, radius))
    point_camera(fill, fill.location, center)
    preview_dir = project / "build/previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    views = {
        "base_front": center + Vector((0, -radius * 2.4, size.z * 0.05)),
        "base_side": center + Vector((radius * 2.4, 0, size.z * 0.05)),
        "base_perspective": center + Vector((radius * 1.65, -radius * 1.65, radius * 0.75)),
    }
    outputs = []
    for name, location in views.items():
        point_camera(camera, location, center)
        path = preview_dir / f"{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs.append(str(path.relative_to(project)))
    return outputs


def object_summary(objects):
    return [{"name": obj.name, "type": obj.type, "mmd_type": str(getattr(obj, "mmd_type", "NONE"))} for obj in sorted(objects, key=lambda o: o.name)]


def main() -> int:
    args = args_after_separator()
    project = args.project_root.resolve()
    validation = json.loads((project / "build/reports/sven_validation.json").read_text(encoding="utf-8"))
    if not validation.get("passed"):
        raise RuntimeError("Validacao Sven falhou; cena-base nao sera criada")
    pmx_report = json.loads((project / "build/reports/pmx_inventory.json").read_text(encoding="utf-8"))

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    original_collection = ensure_collection("Arabella_Original")
    result_collection = ensure_collection("Arabella_Result")
    reference_collection = ensure_collection("Sven_Reference")
    setup_collection = ensure_collection("Scene_Setup")

    pmx_path = project / "assets/source/Arabella.pmx"
    original_objects = import_pmx(pmx_path, original_collection)
    original_collection.hide_viewport = True
    original_collection.hide_render = True
    working_objects = import_pmx(pmx_path, result_collection)
    working_names = {obj.name for obj in working_objects}
    cleanup = cleanup_working_copy(working_objects)
    working_objects = [bpy.data.objects[name] for name in working_names if name in bpy.data.objects]
    material_assignments = create_preview_materials(working_objects, project, pmx_report)

    # MMD Tools ja converte PMX (X,Y,Z) para Blender (X,Z,Y). Para testar
    # GoldSrc (X,-Z,Y), resta refletir o eixo Y e aplicar a escala estimada.
    alignment = bpy.data.objects.new("Arabella_Alignment_PREVIEW_NOT_APPLIED", None)
    setup_collection.objects.link(alignment)
    alignment.scale = (3.48, -3.48, 3.48)
    roots = [obj for obj in working_objects if obj.parent is None]
    for obj in roots:
        obj.parent = alignment

    torso_objects, _ = import_smd(project / "assets/sven/reference/HEV_male_torso_reference.smd", reference_collection, "NEW_ARMATURE", False)
    bip01 = selected_armature(torso_objects)
    bip01.name = "Bip01"
    bip01.data.name = "Bip01"
    legs_objects, _ = import_smd(project / "assets/sven/reference/HEV_male_legs_reference.smd", reference_collection, "APPEND", False, bip01)
    reference_objects = list({*torso_objects, *legs_objects})
    style_reference(reference_objects)
    _, wave_actions = import_smd(project / "assets/sven/player_anims/action_wave.smd", reference_collection, "APPEND", True, bip01)
    if wave_actions:
        wave_actions[0].name = "action_wave"

    scene = bpy.context.scene
    scene.frame_start = 0
    scene.frame_end = max(1, int(validation["animations"]["details"][next(i for i, x in enumerate(validation["animations"]["details"]) if x["name"].casefold() == "action_wave.smd")]["frame_max"] or 1))
    scene.frame_set(scene.frame_start)
    bpy.context.view_layer.update()
    pre_align_bbox = world_bbox(working_objects)
    reference_bbox = world_bbox(reference_objects)
    sole_offset = reference_bbox["min"][2] - pre_align_bbox["min"][2]
    alignment.location.z += sole_offset
    bpy.context.view_layer.update()
    previews = render_previews(project, working_objects + reference_objects)

    report = {
        "blender_version": bpy.app.version_string,
        "collections": ["Arabella_Original", "Arabella_Result", "Sven_Reference", "Scene_Setup"],
        "original_hidden": original_collection.hide_render and original_collection.hide_viewport,
        "original_objects": object_summary(original_objects),
        "working_objects": object_summary(working_objects),
        "reference_objects": object_summary(reference_objects),
        "bip01_armatures": [obj.name for obj in bpy.data.objects if obj.type == "ARMATURE" and obj.name == "Bip01"],
        "bip01_bones": len(bip01.data.bones),
        "test_action": "action_wave",
        "actions": [action.name for action in bpy.data.actions],
        "cleanup": cleanup,
        "collapse_expression_policy": "retained; pending visual confirmation from generated previews",
        "texture_assignments": material_assignments,
        "coordinate_test": {
            "raw_requested": "(X,Y,Z) -> (X,-Z,Y)",
            "mmd_tools_import": "(X,Y,Z) -> (X,Z,Y)",
            "remaining_parent_transform": "scale (3.48,-3.48,3.48)",
            "preview_sole_alignment_z": sole_offset,
            "applied_to_mesh_data": False,
        },
        "arabella_bbox_after_preview_parent": world_bbox(working_objects),
        "sven_reference_bbox": world_bbox(reference_objects),
        "previews": previews,
    }
    report_path = project / "build/reports/blender_scene.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checkpoint = project / "blender/checkpoints/phase_02_scene_base.blend"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(checkpoint), check_existing=False)
    bpy.ops.wm.save_as_mainfile(filepath=str(project / "blender/arabella_work.blend"), check_existing=False)
    print(json.dumps({"previews": previews, "bip01_bones": len(bip01.data.bones), "blend": "blender/arabella_work.blend"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
