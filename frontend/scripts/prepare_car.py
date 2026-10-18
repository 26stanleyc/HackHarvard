"""Prepare the fleet car for the Results screen from Khronos' "Car Concept" glTF sample.

Source: https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CarConcept
"Car Concept" by Eric Chadwick, Darmstadt Graphics Group GmbH, CC BY 4.0, started from a
public-domain model by Unity Fan. The Khronos and 3D Commerce logos are not covered by that
license, so this script removes them. The app shows the credit line under the fleet.

Download CarConcept.glb (glTF-Binary folder of the link above), then run with Blender's Python
module (``pip install bpy``, Python 3.13) or Blender itself:

    ~/.venvs/bpy/bin/python frontend/scripts/prepare_car.py path/to/CarConcept.glb
    blender -b -P frontend/scripts/prepare_car.py -- path/to/CarConcept.glb

Writes ``frontend/public/models/car.glb`` with two objects facing +X on the ground, about
LENGTH m long: ``car`` (detailed) and ``car_lite`` (for large fleets). Materials the app looks
up by name: ``Body`` (main paint, replaced by the app's shared paint), ``Headlight`` and
``Taillight`` (switched on by the app). Pass ``--preview out.png`` to render both.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

OUT = Path(__file__).resolve().parents[1] / "public" / "models" / "car.glb"
LENGTH = 4.6  # metres, nose to tail
# never visible from the fleet camera, or a logo carrier
DROP_PREFIXES = ("Interior", "Engine")
DROP_PARTS = ("Under", "HoodInterior", "Wiper", "License")
DECIMATE = {"Rim": 0.3, "BrakePad": 0.4}  # the densest detail parts, before joining
LITE_RATIO = 0.35  # car_lite = car decimated to this share of its triangles
RENAME = {"Paint 1 Carmine": "Body", "Paint 2 Carmine": "Accent", "Brakelight": "Taillight"}


def principled(mat: bpy.types.Material) -> bpy.types.Node:
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def unlink(mat: bpy.types.Material, socket: str) -> None:
    inp = principled(mat).inputs[socket]
    for link in list(inp.links):
        mat.node_tree.links.remove(link)


def strip_logos() -> None:
    """The Khronos logo is a 512x128 texture used as emission on trim, rims, mirrors and
    brakes; the tire sidewall color and normal maps carry the Khronos and 3D Commerce logos."""
    logo = {img.name for img in bpy.data.images if tuple(img.size) == (512, 128)}
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        bsdf = principled(mat)
        src = [l.from_node for l in bsdf.inputs["Emission Color"].links]
        if any(getattr(n, "image", None) and n.image.name in logo for n in src):
            unlink(mat, "Emission Color")
            bsdf.inputs["Emission Color"].default_value = (0, 0, 0, 1)
            bsdf.inputs["Emission Strength"].default_value = 0.0
    side = bpy.data.materials.get("Tireside")
    if side:
        unlink(side, "Base Color")
        unlink(side, "Normal")
        principled(side).inputs["Base Color"].default_value = (0.012, 0.012, 0.012, 1)


def tune_materials() -> None:
    glass = bpy.data.materials["Glass"]  # dark tinted, opaque: cheap to render, hides the cabin
    bsdf = principled(glass)
    bsdf.inputs["Base Color"].default_value = (0.012, 0.014, 0.017, 1)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 0.04
    bsdf.inputs["Transmission Weight"].default_value = 0.0
    bsdf.inputs["Coat Weight"].default_value = 1.0
    sig = bpy.data.materials.get("Signallight")  # turn signals were always lit
    if sig:
        principled(sig).inputs["Emission Strength"].default_value = 0.0
    for old, new in RENAME.items():
        if old in bpy.data.materials:
            bpy.data.materials[old].name = new


def apply_decimate(obj: bpy.types.Object, ratio: float) -> None:
    mod = obj.modifiers.new("decimate", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    depsgraph = bpy.context.evaluated_depsgraph_get()
    old = obj.data
    obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    obj.modifiers.clear()
    if old.users == 0:
        bpy.data.meshes.remove(old)


def triangles(obj: bpy.types.Object) -> int:
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def build() -> tuple[bpy.types.Object, bpy.types.Object]:
    # Unparent everything first: the parts hang off BodyUnderside, which is dropped below and
    # carries the import's up-axis rotation, so deleting it first would tip them over.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        if obj.name.startswith(DROP_PREFIXES) or any(p in obj.name for p in DROP_PARTS):
            bpy.data.objects.remove(obj)
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()  # instanced wheels: decimate each copy on its own
        for part, ratio in DECIMATE.items():
            if part in obj.name:
                apply_decimate(obj, ratio)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.join()
    car = bpy.context.view_layer.objects.active
    for o in [o for o in bpy.context.scene.objects if o != car]:
        bpy.data.objects.remove(o)  # empties and parents left from the import
    car.name = car.data.name = "car"

    # Bake transforms into the mesh (transform_apply is unreliable in background mode), then
    # face +X (the model faces -Y), scale to LENGTH, sit on the ground, centre on the origin.
    car.data.transform(car.matrix_world)
    car.matrix_world = Matrix.Identity(4)
    car.data.transform(Matrix.Rotation(math.pi / 2, 4, "Z"))
    xs = [v.co.x for v in car.data.vertices]
    car.data.transform(Matrix.Scale(LENGTH / (max(xs) - min(xs)), 4))
    lo = Vector((min(v.co[i] for v in car.data.vertices) for i in range(3)))
    hi = Vector((max(v.co[i] for v in car.data.vertices) for i in range(3)))
    car.data.transform(Matrix.Translation(-Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))))

    lite = car.copy()
    lite.data = car.data.copy()
    lite.name = lite.data.name = "car_lite"
    bpy.context.collection.objects.link(lite)
    apply_decimate(lite, LITE_RATIO)
    for img in bpy.data.images:  # the 2K ambient-occlusion map is plenty at 1K
        if img.size[0] > 1024:
            img.scale(1024, 1024 * img.size[1] // img.size[0])
    return car, lite


def render_preview(cars: list[bpy.types.Object], path: str) -> None:
    for i, c in enumerate(cars):
        c.location = (0, (i - 0.5) * 3.2, 0)
    body = bpy.data.materials["Body"]
    principled(body).inputs["Base Color"].default_value = (0.82, 0.84, 0.85, 1)
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_plane_add(size=60)
    floor = bpy.context.active_object
    fm = bpy.data.materials.new("floor")
    principled(fm).inputs["Base Color"].default_value = (0.5, 0.5, 0.48, 1)
    floor.data.materials.append(fm)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.location = (8.5, -6.5, 3.2)
    cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    sun.data.energy = 3.0
    scene.collection.objects.link(sun)
    scene.world = bpy.data.worlds.new("world")
    scene.world.color = (0.7, 0.72, 0.75)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.render.resolution_x, scene.render.resolution_y = 1400, 760
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    preview = argv[argv.index("--preview") + 1] if "--preview" in argv else None
    src = next(a for a in argv if a.lower().endswith(".glb"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    strip_logos()
    tune_materials()
    car, lite = build()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    car.select_set(True)
    lite.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(OUT), export_format="GLB", use_selection=True,
                              # PNG/JPEG as imported: WebP cannot hold the 1-channel AO map
                              export_apply=True, export_yup=True, export_image_format="AUTO",
                              export_meshopt_compression_enable=True)
    dims = tuple(round(d, 2) for d in car.dimensions)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB): car {triangles(car)} triangles, "
          f"car_lite {triangles(lite)}; size x/y/z {dims} m; materials "
          f"{sorted(s.material.name for s in car.material_slots if s.material)}")
    if preview:
        render_preview([car, lite], preview)
        print(f"preview {preview}")


if __name__ == "__main__":
    main()
