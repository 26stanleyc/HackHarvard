"""Build the herding-effect explainer scene and save herding.blend next to this file.

Build:  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python build_herding.py
Render: /Applications/Blender.app/Contents/MacOS/Blender -b herding.blend -a

HERDING_CAPTIONS=0 builds herding_nocap.blend without the on-screen title and captions,
for the website's scroll-scrubbed version (the page draws its own captions in HTML).

Story (24 fps, 20 s):
  0  Four companies hold flexible load; green columns are spare clean energy per hour.
  1  A standard carbon-aware tool sends everyone to the same two "cleanest" hours.
  2  Those hours run out of clean energy; overflow turns red and fossil plants ramp up.
  3  The clearinghouse schedules the bids jointly, so every block fits inside clean energy.
  4  Each company is paid its Shapley share of the carbon that coordination avoided.
The capacities and block counts are illustrative, not output from the simulator.
"""
import math
import os

import bmesh
import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTIONS = os.environ.get("HERDING_CAPTIONS", "1") != "0"
STEM = "herding" if CAPTIONS else "herding_nocap"
FPS = 24
END = 480

# Site palette (frontend/src/app/globals.css)
PAPER = "#f6f5f0"
INK = "#15191a"
INK2 = "#464b48"
MUTED = "#74776f"
ACCENT = "#0f5c45"
DANGER = "#a1281c"
CLEAN = "#3f9a72"
SMOKE = "#a3a097"
COMPANIES = [
    ("EV fleet", "#2f5d8a"),
    ("Data center", "#7a5aa6"),
    ("Office pre-cooling", "#c98a1b"),
    ("Cold storage", "#8c6d4f"),
]

SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
SANS = "/System/Library/Fonts/Supplemental/Arial.ttf"
SANS_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# Chart geometry
W = 0.62  # column pitch along x (one hour)
P = 0.32  # stacking pitch (one block of load)
BS = 0.5  # block footprint
BH = 0.28  # block height
CAP = [1, 1, 1, 1, 1, 1, 0, 0, 1, 2, 3, 4, 4, 4, 4, 3, 2, 1, 0, 0, 0, 1, 1, 1]
HERD_HOURS = (12, 13)


def hx(h):
    return (h - 11.5) * W


def lin(hexstr):
    h = hexstr.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i : i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (*out, 1.0)


# ---------------------------------------------------------------- scene setup
scene = bpy.context.scene
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

scene.frame_start = 1
scene.frame_end = END
scene.render.fps = FPS
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 100
scene.eevee.taa_render_samples = 48
scene.eevee.use_shadows = True
scene.eevee.use_raytracing = True
scene.eevee.use_fast_gi = True
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

world = bpy.data.worlds.new("Paper")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = lin(PAPER)
bg.inputs["Strength"].default_value = 1.0

fonts = {
    "serif": bpy.data.fonts.load(SERIF),
    "sans": bpy.data.fonts.load(SANS),
    "sans_bold": bpy.data.fonts.load(SANS_BOLD),
}


# ---------------------------------------------------------------- helpers
def principled(name, hexc, rough=0.55, alpha=None, emit=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = lin(hexc)
    p.inputs["Roughness"].default_value = rough
    if alpha is not None:
        p.inputs["Alpha"].default_value = alpha
        m.surface_render_method = "BLENDED"
        m.use_backface_culling = True
    if emit is not None:
        p.inputs["Emission Color"].default_value = lin(emit)
        p.inputs["Emission Strength"].default_value = 0.0
    return m


def flat(name, hexc):
    """Unlit material that renders the exact hex colour, with an animatable opacity."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = lin(hexc)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 1.0
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    m.surface_render_method = "BLENDED"
    return m, mix.inputs[0]


def key(socket_or_obj, path, frame, value=None):
    if value is not None:
        if path == "default_value":
            socket_or_obj.default_value = value
        else:
            setattr(socket_or_obj, path, value)
    socket_or_obj.keyframe_insert(path, frame=frame)


def fade(opacity, f_in, f_out=None, d=10):
    """Fade in over d frames starting at f_in; fade out ending at f_out."""
    if f_in <= 1:
        key(opacity, "default_value", 1, 1.0)
    else:
        key(opacity, "default_value", f_in, 0.0)
        key(opacity, "default_value", f_in + d, 1.0)
    if f_out is not None:
        key(opacity, "default_value", f_out - d, 1.0)
        key(opacity, "default_value", f_out, 0.0)


def link(ob):
    scene.collection.objects.link(ob)
    return ob


def text(body, size, font, mat, loc, align="LEFT", width=None, parent=None, face=None, name=None):
    cu = bpy.data.curves.new(name or body[:24], "FONT")
    cu.body = body
    cu.size = size
    cu.font = fonts[font]
    cu.align_x = align
    if width:
        cu.text_boxes[0].width = width
    ob = link(bpy.data.objects.new(name or body[:24], cu))
    ob.data.materials.append(mat)
    ob.location = loc
    if parent is not None:
        ob.parent = parent
    if face is not None:
        c = ob.constraints.new("COPY_ROTATION")
        c.target = face
    return ob


def box(name, sx, sy, sz, loc, mat, bevel=0.03):
    """Box with its origin at the bottom centre, so scaling z grows it upward."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z = (v.co.z + 0.5) * sz
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    me.materials.append(mat)
    if bevel:
        b = ob.modifiers.new("Bevel", "BEVEL")
        b.width = bevel
        b.segments = 3
        b.limit_method = "ANGLE"
    return ob


def cylinder(name, r, h, loc, mat):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=r, radius2=r, depth=h)
    for v in bm.verts:
        v.co.z += h / 2
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    me.materials.append(mat)
    return ob


def sphere(name, r, loc, mat):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=r)
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    me.materials.append(mat)
    return ob


# ---------------------------------------------------------------- floor (seamless cyclorama)
verts, faces = [], []
profile = [(y, 0.0) for y in range(-40, 9, 2)]
R = 8.0
for i in range(1, 13):
    a = i / 12 * math.pi / 2
    profile.append((8 + R * math.sin(a), R - R * math.cos(a)))
profile.append((8 + R, 40.0))
for x in (-60.0, 60.0):
    for y, z in profile:
        verts.append((x, y, z))
n = len(profile)
for i in range(n - 1):
    faces.append((i, i + 1, n + i + 1, n + i))
me = bpy.data.meshes.new("Cyc")
me.from_pydata(verts, [], faces)
for poly in me.polygons:
    poly.use_smooth = True
floor = link(bpy.data.objects.new("Cyc", me))
me.materials.append(principled("Floor", PAPER, rough=0.95))

# ---------------------------------------------------------------- lights
sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 2.2
sun_data.angle = math.radians(12)
sun = link(bpy.data.objects.new("Sun", sun_data))
sun.rotation_euler = (math.radians(42), math.radians(8), math.radians(-28))

# ---------------------------------------------------------------- camera
target = link(bpy.data.objects.new("CamTarget", None))
target.location = (0.0, 0.4, 1.7)
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 37
cam_data.clip_end = 300
cam = link(bpy.data.objects.new("Camera", cam_data))
scene.camera = cam
tc = cam.constraints.new("TRACK_TO")
tc.target = target
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"
key(cam, "location", 1, (-0.9, -16.6, 6.7))
key(cam, "location", END, (0.3, -15.6, 6.3))

# ---------------------------------------------------------------- clean-energy columns
# Solid bars sit just behind each hour's block column, so each stack reads
# against that hour's clean-energy ceiling.
col_mat = principled("CleanBar", CLEAN, rough=0.5)
BAR_Y = 0.42
for h, c in enumerate(CAP):
    if c == 0:
        continue
    col = box(f"Clean_{h:02d}", 0.58, 0.22, c * P, (hx(h), BAR_Y, 0.0), col_mat, bevel=0.02)
    f0 = 8 + int(h * 1.5)
    key(col, "scale", 1, (1, 1, 0.001))
    key(col, "scale", f0, (1, 1, 0.001))
    key(col, "scale", f0 + 20, (1, 1, 1))

# ---------------------------------------------------------------- labels in the scene
label_ink, _ = flat("LabelInk", INK2)
label_muted, _ = flat("LabelMuted", MUTED)
label_green, _ = flat("LabelGreen", ACCENT)
for h in (0, 6, 12, 18, 23):
    text(f"{h:02d}:00", 0.26, "sans", label_muted, (hx(h), -0.75, 0.02), align="CENTER", face=cam)
text("Spare clean energy", 0.3, "sans_bold", label_green, (hx(2.5), 0.0, 0.62), align="CENTER", face=cam)

# ---------------------------------------------------------------- fossil plants
plant_mat = principled("Plant", INK2, rough=0.7)
glow_mat = principled("PlantGlow", "#5a5d58", rough=0.6, emit=DANGER)
glow = glow_mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"]
key(glow, "default_value", 1, 0.0)
key(glow, "default_value", 168, 0.0)
key(glow, "default_value", 190, 9.0)
key(glow, "default_value", 296, 9.0)
key(glow, "default_value", 322, 0.0)
smoke_mat = principled("Smoke", SMOKE, rough=1.0, alpha=0.8)
PLANT_X = (4.9, 5.75, 6.6)
PLANT_Y = 3.7
box("PlantHall", 2.9, 1.3, 0.9, (5.75, PLANT_Y + 0.3, 0), plant_mat, bevel=0.04)
STACK_H = 2.6
for s, x in enumerate(PLANT_X):
    cylinder(f"Stack_{s}", 0.2, STACK_H, (x, PLANT_Y, 0), plant_mat)
    cylinder(f"StackGlow_{s}", 0.215, 0.16, (x, PLANT_Y, STACK_H - 0.22), glow_mat)
    for n_puff, f0 in enumerate(range(174 + 3 * s, 300, 9)):
        p = sphere(f"Puff_{s}_{n_puff:02d}", 0.5, (x, PLANT_Y, STACK_H), smoke_mat)
        drift = -0.35 - 0.1 * ((n_puff + s) % 3)
        key(p, "scale", 1, (0, 0, 0))
        key(p, "scale", f0 - 1, (0, 0, 0))
        key(p, "scale", f0, (0.18, 0.18, 0.18))
        key(p, "location", f0, (x, PLANT_Y, STACK_H + 0.05))
        key(p, "scale", f0 + 30, (0.5, 0.5, 0.5))
        key(p, "location", f0 + 30, (x + drift, PLANT_Y + 0.2, STACK_H + 1.5))
        key(p, "scale", f0 + 46, (0, 0, 0))
        key(p, "location", f0 + 46, (x + drift * 1.5, PLANT_Y + 0.3, STACK_H + 2.2))
plant_label, _ = flat("PlantLabel", PAPER)
pl = text("Gas & coal plants", 0.24, "sans_bold", plant_label, (5.75, PLANT_Y - 0.36, 0.36), align="CENTER")
pl.rotation_euler = (math.radians(90), 0, 0)

# ---------------------------------------------------------------- company blocks
STAGE_X = (-5.0, -1.7, 1.7, 5.0)
STAGE_Y = -2.7
blocks = []  # (object, base-colour socket, company hex)
for i in range(16):
    c = i % 4
    j = i // 4
    name, hexc = COMPANIES[c]
    m = principled(f"Block_{i:02d}", hexc, rough=0.45)
    ob = box(f"Block_{i:02d}", BS, BS, BH, (0, 0, 0), m, bevel=0.035)
    start = (STAGE_X[c] + (j % 2 - 0.5) * 0.62, STAGE_Y + (j // 2 - 0.5) * 0.62, 0.0)
    ob.location = start
    blocks.append((ob, m.node_tree.nodes["Principled BSDF"].inputs["Base Color"], hexc, start))

for c, (name, hexc) in enumerate(COMPANIES):
    m, _ = flat(f"Legend_{c}", INK2)
    text(name, 0.3, "sans_bold", m, (STAGE_X[c] + 0.2, STAGE_Y - 1.0, 0.16), align="CENTER", face=cam)
    # Chip keeps the legend readable after the blocks have left.
    chip_x = STAGE_X[c] + 0.2 - len(name) * 0.075 - 0.08
    box(f"Chip_{c}", 0.26, 0.26, 0.12, (chip_x, STAGE_Y - 1.0, 0), principled(f"Chip_{c}", hexc, rough=0.45), bevel=0.02)

# Arrival order mixes the companies within each herd stack.
ORDER = [0, 5, 10, 15, 1, 6, 11, 12, 2, 7, 8, 13, 3, 4, 9, 14]
herd = {}  # block index -> (hour, level)
for k, b in enumerate(ORDER):
    herd[b] = (HERD_HOURS[k % 2], k // 2)


def pos(hour, level):
    return (hx(hour), 0.0, level * P)


def fly(ob, f0, dur, a, b, lift=1.1):
    key(ob, "location", f0, a)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, max(a[2], b[2]) + lift)
    key(ob, "location", f0 + dur // 2, mid)
    key(ob, "location", f0 + dur, b)


# Act 1: herd into hours 12-13 (frames 90-170)
for k, b in enumerate(ORDER):
    ob, color, hexc, start = blocks[b]
    key(color, "default_value", 1, lin(hexc))
    fly(ob, 92 + k * 4, 22, start, pos(*herd[b]), lift=1.4)

# Act 2: blocks above the clean ceiling go red (frames 170-200)
overflow = [b for b in ORDER if herd[b][1] >= CAP[herd[b][0]]]
for n_red, b in enumerate(sorted(overflow, key=lambda b: herd[b][1])):
    ob, color, hexc, start = blocks[b]
    f = 170 + n_red * 3
    key(color, "default_value", f, lin(hexc))
    key(color, "default_value", f + 10, lin(DANGER))

# Act 3: coordinator spreads the blocks under the clean ceiling (frames 292-370).
# Bottom three blocks of each herd stack stay; the rest move, top first.
slots = [(11, 0), (14, 0), (10, 0), (15, 0), (9, 0), (16, 0), (11, 1), (14, 1), (10, 1), (11, 2)]
movers = sorted([b for b in ORDER if herd[b][1] >= 3], key=lambda b: -herd[b][1])
for m_i, b in enumerate(movers):
    ob, color, hexc, start = blocks[b]
    f0 = 292 + m_i * 5
    fly(ob, f0, 24, pos(*herd[b]), pos(*slots[m_i]), lift=0.6)
    if b in overflow:
        key(color, "default_value", f0, lin(DANGER))
        key(color, "default_value", f0 + 12, lin(hexc))

# ---------------------------------------------------------------- captions (screen space)
def build_captions():
    # At lens 37 and 4 units in front of the camera the frame spans 3.89 x 2.19 units.
    D = -4.0
    LEFT = -1.78
    title_mat, title_op = flat("Title", INK)
    text("The herding effect", 0.15, "serif", title_mat, (LEFT, 0.87, D), parent=cam)
    fade(title_op, 1, 392, d=12)
    brand_mat, brand_op = flat("Brand", ACCENT)
    text("Carbon Clearinghouse", 0.15, "serif", brand_mat, (LEFT, 0.87, D), parent=cam)
    fade(brand_op, 396, None, d=14)

    lines = [
        (1, 86, "Four companies each have flexible load to schedule for tomorrow. "
                "The green columns are the spare clean energy available in each hour."),
        (90, 164, "1   A standard carbon-aware tool sends every company to the same "
                  "“cleanest” hours."),
        (168, 286, "2   Those hours run out of clean energy, so fossil plants "
                   "ramp up to cover the rush."),
        (290, 392, "3   The clearinghouse schedules all the bids together, so each load "
                   "fits inside clean energy."),
        (396, None, "Each company is paid its Shapley share of the carbon that "
                    "coordination avoided."),
    ]
    for n_cap, (f_in, f_out, body) in enumerate(lines):
        m, op = flat(f"Caption_{n_cap}", INK2)
        text(body, 0.075, "sans", m, (LEFT, 0.71, D), width=2.3, parent=cam, name=f"Caption_{n_cap}")
        fade(op, f_in, f_out, d=10)


if CAPTIONS:
    build_captions()

# ---------------------------------------------------------------- output
scene.render.image_settings.media_type = "VIDEO"
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "HIGH"
scene.render.ffmpeg.ffmpeg_preset = "GOOD"
scene.render.filepath = os.path.join(HERE, STEM + "_")
scene.frame_set(1)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, STEM + ".blend"))
print("BUILD OK", len(bpy.data.objects), "objects")
