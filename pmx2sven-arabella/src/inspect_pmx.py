#!/usr/bin/env python3
"""Read PMX metadata and geometry without changing or importing the model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.encoding = "utf-8"

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.data):
            raise ValueError(f"PMX truncado no offset {self.offset}")
        chunk = self.data[self.offset:end]
        self.offset = end
        return chunk

    def unpack(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        values = struct.unpack("<" + fmt, self.take(size))
        return values[0] if len(values) == 1 else values

    def string(self) -> str:
        size = self.unpack("i")
        if size < 0:
            raise ValueError("Comprimento de string PMX negativo")
        return self.take(size).decode(self.encoding, errors="replace")

    def index(self, size: int, signed: bool = True) -> int:
        formats = {(1, True): "b", (1, False): "B", (2, True): "h", (2, False): "H", (4, True): "i", (4, False): "I"}
        return int(self.unpack(formats[(size, signed)]))


def vec_minmax(points: list[tuple[float, float, float]]) -> dict:
    if not points:
        return {"min": None, "max": None, "size": None}
    mins = [min(p[i] for p in points) for i in range(3)]
    maxs = [max(p[i] for p in points) for i in range(3)]
    return {"min": mins, "max": maxs, "size": [maxs[i] - mins[i] for i in range(3)]}


def parse(path: Path) -> dict:
    raw = path.read_bytes()
    r = Reader(raw)
    if r.take(4) != b"PMX ":
        raise ValueError("Assinatura PMX invalida")
    version = float(r.unpack("f"))
    header_size = int(r.unpack("B"))
    header = list(r.take(header_size))
    if len(header) < 8:
        raise ValueError(f"Cabecalho PMX inesperado: {header_size} bytes")
    text_encoding, additional_uv, vertex_idx, texture_idx, material_idx, bone_idx, morph_idx, rigid_idx = header[:8]
    r.encoding = "utf-16-le" if text_encoding == 0 else "utf-8"
    model_text = {
        "name_local": r.string(), "name_english": r.string(),
        "comment_local": r.string(), "comment_english": r.string(),
    }

    vertex_count = int(r.unpack("i"))
    positions: list[tuple[float, float, float]] = []
    vertex_influences: list[list[tuple[int, float]]] = []
    weight_types = Counter()
    weight_names = {0: "BDEF1", 1: "BDEF2", 2: "BDEF4", 3: "SDEF", 4: "QDEF"}
    for _ in range(vertex_count):
        pos = tuple(float(v) for v in r.unpack("3f"))
        positions.append(pos)
        r.take(12 + 8 + additional_uv * 16)  # normal, UV, UVs extras
        deform = int(r.unpack("B"))
        weight_types[weight_names.get(deform, f"UNKNOWN_{deform}")] += 1
        influences: list[tuple[int, float]]
        if deform == 0:
            influences = [(r.index(bone_idx), 1.0)]
        elif deform in (1, 3):
            bones = [r.index(bone_idx), r.index(bone_idx)]
            weight = float(r.unpack("f"))
            influences = [(bones[0], weight), (bones[1], 1.0 - weight)]
            if deform == 3:
                r.take(36)
        elif deform in (2, 4):
            bones = [r.index(bone_idx) for _ in range(4)]
            weights = [float(v) for v in r.unpack("4f")]
            influences = list(zip(bones, weights))
        else:
            raise ValueError(f"Tipo de deformacao PMX desconhecido: {deform}")
        vertex_influences.append([(b, w) for b, w in influences if b >= 0 and w > 0.0])
        r.take(4)  # edge scale

    index_count = int(r.unpack("i"))
    face_indices = [r.index(vertex_idx, signed=False) for _ in range(index_count)]
    textures = [r.string() for _ in range(int(r.unpack("i")))]

    materials = []
    material_count = int(r.unpack("i"))
    face_cursor = 0
    for material_number in range(material_count):
        name_local, name_english = r.string(), r.string()
        r.take(16 + 12 + 4 + 12)  # diffuse, specular, power, ambient
        flags = int(r.unpack("B"))
        r.take(16 + 4)  # edge color and size
        texture_number = r.index(texture_idx)
        sphere_number = r.index(texture_idx)
        sphere_mode = int(r.unpack("B"))
        shared_toon = int(r.unpack("B"))
        toon_number = r.index(texture_idx) if shared_toon == 0 else int(r.unpack("B"))
        memo = r.string()
        surface_index_count = int(r.unpack("i"))
        subset = face_indices[face_cursor:face_cursor + surface_index_count]
        face_cursor += surface_index_count
        used = sorted(set(subset))
        material_points = [positions[i] for i in used if 0 <= i < len(positions)]
        texture_path = textures[texture_number] if 0 <= texture_number < len(textures) else None
        materials.append({
            "index": material_number,
            "name_local": name_local,
            "name_english": name_english,
            "texture_index": texture_number,
            "texture_path": texture_path,
            "texture_basename": Path(texture_path.replace("\\", "/")).name if texture_path else None,
            "sphere_texture_index": sphere_number,
            "sphere_mode": sphere_mode,
            "toon_index": toon_number,
            "flags": flags,
            "memo": memo,
            "surface_indices": surface_index_count,
            "triangles": surface_index_count // 3,
            "unique_vertices": len(used),
            "bounding_box": vec_minmax(material_points),
        })

    bone_count = int(r.unpack("i"))
    bones = []
    for bone_number in range(bone_count):
        name_local, name_english = r.string(), r.string()
        position = list(r.unpack("3f"))
        parent = r.index(bone_idx)
        layer = int(r.unpack("i"))
        flags = int(r.unpack("H"))
        tail = r.index(bone_idx) if flags & 0x0001 else list(r.unpack("3f"))
        inherit_parent, inherit_weight = None, None
        if flags & (0x0100 | 0x0200):
            inherit_parent, inherit_weight = r.index(bone_idx), float(r.unpack("f"))
        if flags & 0x0400:
            r.take(12)
        if flags & 0x0800:
            r.take(24)
        if flags & 0x2000:
            r.take(4)
        ik = None
        if flags & 0x0020:
            target = r.index(bone_idx)
            loops, angle = int(r.unpack("i")), float(r.unpack("f"))
            link_count = int(r.unpack("i"))
            links = []
            for _ in range(link_count):
                link_bone = r.index(bone_idx)
                limited = int(r.unpack("B"))
                limits = None
                if limited:
                    limits = {"min": list(r.unpack("3f")), "max": list(r.unpack("3f"))}
                links.append({"bone": link_bone, "limits": limits})
            ik = {"target": target, "loops": loops, "angle_limit": angle, "links": links}
        bones.append({
            "index": bone_number, "name_local": name_local, "name_english": name_english,
            "position": position, "parent_index": parent, "layer": layer, "flags": flags,
            "tail": tail, "inherit_parent": inherit_parent, "inherit_weight": inherit_weight, "ik": ik,
        })

    morph_count = int(r.unpack("i"))
    morphs = []
    for morph_number in range(morph_count):
        local, english = r.string(), r.string()
        panel, morph_type, count = int(r.unpack("B")), int(r.unpack("B")), int(r.unpack("i"))
        for _ in range(count):
            if morph_type in (0, 9):
                r.index(morph_idx); r.take(4)
            elif morph_type == 1:
                r.index(vertex_idx, signed=False); r.take(12)
            elif morph_type == 2:
                r.index(bone_idx); r.take(28)
            elif 3 <= morph_type <= 7:
                r.index(vertex_idx, signed=False); r.take(16)
            elif morph_type == 8:
                r.index(material_idx); r.take(1 + 28 * 4)
            elif morph_type == 10:
                r.index(rigid_idx); r.take(1 + 24)
            else:
                raise ValueError(f"Tipo de morph PMX desconhecido: {morph_type}")
        morphs.append({"index": morph_number, "name_local": local, "name_english": english, "panel": panel, "type": morph_type, "offsets": count})

    display_frame_count = int(r.unpack("i"))
    for _ in range(display_frame_count):
        r.string(); r.string(); r.take(1)
        element_count = int(r.unpack("i"))
        for _ in range(element_count):
            kind = int(r.unpack("B"))
            r.index(bone_idx if kind == 0 else morph_idx)

    rigid_body_count = int(r.unpack("i"))
    for _ in range(rigid_body_count):
        r.string(); r.string(); r.index(bone_idx)
        r.take(1 + 2 + 1 + 12 + 12 + 12 + 20 + 1)

    joint_count = int(r.unpack("i"))
    for _ in range(joint_count):
        r.string(); r.string(); r.take(1)
        r.index(rigid_idx); r.index(rigid_idx); r.take(8 * 12)

    soft_body_count = None
    if version >= 2.1 and r.offset < len(raw):
        soft_body_count = int(r.unpack("i"))

    influence_counts = Counter()
    influence_weight_sums = Counter()
    for influences in vertex_influences:
        for bone, weight in influences:
            influence_counts[bone] += 1
            influence_weight_sums[bone] += weight
    vertex_groups = []
    for bone_index, count in sorted(influence_counts.items(), key=lambda item: (-item[1], item[0])):
        bone = bones[bone_index] if 0 <= bone_index < len(bones) else None
        vertex_groups.append({
            "bone_index": bone_index,
            "bone_name_local": bone["name_local"] if bone else None,
            "bone_name_english": bone["name_english"] if bone else None,
            "weighted_vertices": count,
            "weight_sum": influence_weight_sums[bone_index],
        })

    finite_positions = all(math.isfinite(value) for pos in positions for value in pos)
    return {
        "file": str(path.resolve()),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pmx_version": round(version, 3),
        "header": {
            "size": header_size, "encoding": r.encoding, "additional_uv": additional_uv,
            "vertex_index_size": vertex_idx, "texture_index_size": texture_idx,
            "material_index_size": material_idx, "bone_index_size": bone_idx,
            "morph_index_size": morph_idx, "rigid_body_index_size": rigid_idx,
        },
        "model": model_text,
        "counts": {
            "vertices": vertex_count, "surface_indices": index_count, "triangles": index_count // 3,
            "textures": len(textures), "materials": material_count, "bones": bone_count,
            "morphs": morph_count, "display_frames": display_frame_count,
            "rigid_bodies": rigid_body_count, "joints": joint_count, "soft_bodies": soft_body_count,
        },
        "weight_distribution": dict(weight_types),
        "textures": [{"index": i, "stored_path": name, "basename": Path(name.replace("\\", "/")).name} for i, name in enumerate(textures)],
        "materials": materials,
        "bounding_box": vec_minmax(positions),
        "positions_finite": finite_positions,
        "bones": bones,
        "vertex_groups": vertex_groups,
        "morphs": morphs,
        "parser": {"bytes_consumed": r.offset, "file_bytes": len(raw), "reached_eof": r.offset == len(raw)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pmx", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = parse(args.pmx)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
