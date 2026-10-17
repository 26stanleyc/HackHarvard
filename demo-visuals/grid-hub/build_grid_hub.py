"""Build the grid-hub explainer scene and save grid_hub.blend next to this file.

Build:  /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python build_grid_hub.py
Render: /Applications/Blender.app/Contents/MacOS/Blender -b grid_hub.blend -a

Story (24 fps, 20 s):
  0-3 s    Sun rises; solar and wind send clean pulses into the grid hub and its core fills.
  3-10 s   Every company's carbon-aware tool picks the same "cleanest" hour (1 PM). All four
           wires fire at once, the core empties, the gas & coal plant ignites and its dark
           pulses reach every company.
  10-17 s  Rewind. The clearinghouse schedules the bids together: companies take turns across
           10 AM-4 PM, the core never empties and the fossil plant stays dark.
  17-20 s  End card: herded (left) vs coordinated (right), Shapley payout caption.
Everything is illustrative; no quantities in the scene come from the simulator.
"""
import math
import os

import bmesh
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 24
END = 480
STEP = 2  # keyframe spacing for sampled animation

SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
SANS = "/System/Library/Fonts/Supplemental/Arial.ttf"
SANS_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# Palette (dark, Tesla-render style)
NIGHT = "#010309"
DAY = "#050c1a"
FLOOR = "#04070d"
METAL = "#10151d"
METAL2 = "#1b222d"
CLEAN = "#1fe8b4"  # teal-green clean energy
CORE = "#2dff8c"  # hub core green
DIRTY = "#7a1206"  # dark smoky red
FIRE = "#ff3a12"
SMOKE = "#3a3533"
WHITE = "#eef3f6"
TEXT = "#c3ccd4"
MUTED = "#76818c"
WARM = "#ffe2a8"
COMPANIES = [
    ("EV fleet depot", "#4aa3ff"),
    ("Data center", "#ac8cff"),
    ("Office tower", "#ffb547"),
    ("Cold storage", "#a6ecff"),
]


def lin(hexstr, mul=1.0):
    h = hexstr.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i : i + 2], 16) / 255
        out.append(mul * (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4))
    return (*out, 1.0)


def mixc(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ss(a, b, x):
    t = clamp((x - a) / (b - a))
    return t * t * (3 - 2 * t)


def bump(f, a, b, ramp):
    return ss(a, a + ramp, f) * (1 - ss(b - ramp, b, f))


def lerpv(a, b, t):
    if isinstance(a, tuple):
        return tuple(lerpv(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t


def keys_interp(keys, f, ease=True):
    if f <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f <= f1:
            t = (f - f0) / (f1 - f0)
            if ease:
                t = t * t * (3 - 2 * t)
            return lerpv(v0, v1, t)
    return keys[-1][1]


# ---------------------------------------------------------------- story timeline (frames)
HOUR_KEYS = [(1, 5.0), (72, 9.0), (128, 13.0), (240, 13.6), (262, 9.6), (272, 10.0), (396, 16.0), (480, 16.4)]


def hour(f):
    if 240 <= f <= 272:
        return keys_interp([(240, 13.6), (262, 9.6), (272, 10.0)], f, ease=True)
    return keys_interp(HOUR_KEYS, f, ease=False)


def daylight(f):
    return clamp(math.sin((hour(f) - 6.0) / 12.0 * math.pi) * 1.15)


# Coordinated turns (hour windows) for EV, data center, office, cold storage.
TURNS = [(10.0, 11.8), (11.3, 13.1), (12.6, 14.4), (13.9, 15.8)]
PICK = [10.9, 12.2, 13.5, 14.85]  # where each company's bead sits on the dial once coordinated


def load(c, f):
    """0..1 power drawn by company c."""
    base = 0.06
    if f < 240:
        return base + (1 - base) * ss(126, 136, f)
    if f < 264:
        return base + (1 - base) * (1 - ss(240, 248, f))
    if f > 400:
        return base
    h = hour(f)
    a, b = TURNS[c]
    return base + (1 - base) * ss(a, a + 0.3, h) * (1 - ss(b - 0.3, b, h))


def plant(f):
    return ss(154, 176, f) * (1 - ss(242, 256, f))


def fossil(f):
    return ss(158, 190, f) * (1 - ss(242, 258, f))


def rewind(f):
    return bump(f, 241, 262, 4)


def core_level(f):
    """0..1 fill of the hub core."""
    if f < 262:
        return keys_interp([(1, 0.12), (66, 0.86), (128, 0.9), (168, 0.0), (242, 0.0), (262, 0.86)], f)
    total = sum(load(c, f) for c in range(4))
    return clamp(0.86 - 0.17 * (total - 0.24), 0.5, 0.9)


def flow_speed(base, f, fn):
    """Integrate a speed function (periods per second) into an offset, rewinding backwards."""
    off = 0.0
    out = {}
    for fr in range(1, END + 1):
        r = rewind(fr)
        v = fn(fr) * (1 - r) + (-3.2) * r
        off += v / FPS
        out[fr] = base + off
    return out


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
scene.eevee.taa_render_samples = 32
scene.eevee.use_shadows = True
scene.eevee.use_raytracing = True
scene.eevee.ray_tracing_method = "SCREEN"
scene.eevee.use_fast_gi = False
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

world = bpy.data.worlds.new("Night")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Strength"].default_value = 1.0

fonts = {
    "serif": bpy.data.fonts.load(SERIF),
    "sans": bpy.data.fonts.load(SANS),
    "sans_bold": bpy.data.fonts.load(SANS_BOLD),
}


# ---------------------------------------------------------------- helpers
def key(target, path, frame, value=None):
    if value is not None:
        if path == "default_value":
            target.default_value = value
        else:
            setattr(target, path, value)
    target.keyframe_insert(path, frame=frame)


def keyseries(target, path, fn, frames=None):
    for fr in frames or range(1, END + 1, STEP):
        key(target, path, fr, fn(fr))


def link(ob, parent=None):
    scene.collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def empty(name, loc=(0, 0, 0), scale=1.0, parent=None):
    ob = link(bpy.data.objects.new(name, None), parent)
    ob.location = loc
    ob.scale = (scale, scale, scale)
    return ob


def principled(name, hexc, rough=0.4, metal=0.0, alpha=None, emit=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = lin(hexc)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if alpha is not None:
        p.inputs["Alpha"].default_value = alpha
        m.surface_render_method = "BLENDED"
    if emit is not None:
        p.inputs["Emission Color"].default_value = lin(emit)
        p.inputs["Emission Strength"].default_value = strength
    return m


def bsdf(m):
    return next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def emission_mat(name, hexc, strength=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = lin(hexc)
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m, em


def flat(name, hexc, strength=1.0):
    """Unlit colour with an animatable opacity (for text and overlays)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = lin(hexc)
    em.inputs["Strength"].default_value = strength
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = 1.0
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    m.surface_render_method = "BLENDED"
    return m, mix.inputs[0]


def fade(opacity, f_in, f_out=None, d=10):
    if f_in <= 1:
        key(opacity, "default_value", 1, 1.0)
    else:
        key(opacity, "default_value", 1, 0.0)
        key(opacity, "default_value", f_in, 0.0)
        key(opacity, "default_value", f_in + d, 1.0)
    if f_out is not None:
        key(opacity, "default_value", f_out - d, 1.0)
        key(opacity, "default_value", f_out, 0.0)


def mesh_obj(name, bm, mat, parent=None, smooth=False, loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for poly in me.polygons:
            poly.use_smooth = True
    ob = link(bpy.data.objects.new(name, me), parent)
    ob.location = loc
    if mat is not None:
        me.materials.append(mat)
    return ob


def box(name, sx, sy, sz, loc, mat, parent=None, bevel=0.03, rot=None):
    """Box with origin at bottom centre."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z = (v.co.z + 0.5) * sz
    ob = mesh_obj(name, bm, mat, parent, loc=loc)
    if rot is not None:
        ob.rotation_euler = rot
    if bevel:
        b = ob.modifiers.new("Bevel", "BEVEL")
        b.width = bevel
        b.segments = 2
        b.limit_method = "ANGLE"
    return ob


def cylinder(name, r, h, loc, mat, parent=None, r2=None, segs=32):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r if r2 is None else r2, depth=h)
    for v in bm.verts:
        v.co.z += h / 2
    return mesh_obj(name, bm, mat, parent, smooth=True, loc=loc)


def sphere(name, r, loc, mat, parent=None, segs=32):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2, radius=r)
    return mesh_obj(name, bm, mat, parent, smooth=True, loc=loc)


def torus(name, R, r, loc, mat, parent=None, a0=0.0, a1=2 * math.pi, n=96, m=12):
    """Torus (or arc of one) lying in the XY plane."""
    closed = abs(a1 - a0 - 2 * math.pi) < 1e-6
    rows = n if closed else n + 1
    verts, faces = [], []
    for i in range(rows):
        a = a0 + (a1 - a0) * i / n
        c = Vector((math.cos(a), math.sin(a), 0))
        for j in range(m):
            b = 2 * math.pi * j / m
            p = c * (R + r * math.cos(b)) + Vector((0, 0, r * math.sin(b)))
            verts.append(p)
    for i in range(n):
        i2 = (i + 1) % rows
        for j in range(m):
            j2 = (j + 1) % m
            faces.append((i * m + j, i2 * m + j, i2 * m + j2, i * m + j2))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    for poly in me.polygons:
        poly.use_smooth = True
    ob = link(bpy.data.objects.new(name, me), parent)
    ob.location = loc
    me.materials.append(mat)
    return ob


def text(body, size, font, mat, loc, align="CENTER", parent=None, face=None, name=None, rot=None):
    cu = bpy.data.curves.new(name or body[:24], "FONT")
    cu.body = body
    cu.size = size
    cu.font = fonts[font]
    cu.align_x = align
    cu.align_y = "CENTER"
    ob = link(bpy.data.objects.new(name or body[:24], cu), parent)
    ob.data.materials.append(mat)
    ob.location = loc
    if rot is not None:
        ob.rotation_euler = rot
    if face is not None:
        c = ob.constraints.new("COPY_ROTATION")
        c.target = face
    return ob


def bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return p0 * u**3 + p1 * 3 * u * u * t + p2 * 3 * u * t * t + p3 * t**3


def tube(name, ctrl, r, mat, parent=None, n=96, segs=12):
    """Glowing wire: tube along a cubic Bezier, UV.x = arc length so pulses keep world spacing."""
    p0, p1, p2, p3 = (Vector(c) for c in ctrl)
    pts = [bezier(p0, p1, p2, p3, i / n) for i in range(n + 1)]
    verts, faces, uvs = [], [], []
    s = 0.0
    up = Vector((0, 0, 1))
    for i, p in enumerate(pts):
        if i > 0:
            s += (p - pts[i - 1]).length
        t = (pts[min(i + 1, n)] - pts[max(i - 1, 0)]).normalized()
        side = t.cross(up)
        if side.length < 1e-4:
            side = t.cross(Vector((1, 0, 0)))
        side.normalize()
        nrm = side.cross(t).normalized()
        for j in range(segs):
            b = 2 * math.pi * j / segs
            verts.append(tuple(p + (side * math.cos(b) + nrm * math.sin(b)) * r))
            uvs.append((s, j / segs))
    for i in range(n):
        for j in range(segs):
            j2 = (j + 1) % segs
            faces.append((i * segs + j, i * segs + j2, (i + 1) * segs + j2, (i + 1) * segs + j))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        poly.use_smooth = True
        for li in poly.loop_indices:
            uv.data[li].uv = uvs[me.loops[li].vertex_index]
    ob = link(bpy.data.objects.new(name, me), parent)
    me.materials.append(mat)
    return ob, s


def wire_material(name, clean_hex, dirty_hex, spacing=1.6):
    """Emission travelling along UV.x. Controls: Offset, Bright, Base, Fossil (Value nodes)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = bsdf(m)
    p.inputs["Base Color"].default_value = lin("#06090e")
    p.inputs["Roughness"].default_value = 0.25
    p.inputs["Metallic"].default_value = 0.4
    nodes, links = nt.nodes, nt.links

    def val(label, v):
        n = nodes.new("ShaderNodeValue")
        n.name = n.label = label
        n.outputs[0].default_value = v
        return n.outputs[0]

    offset, bright, base, fos = val("Offset", 0.0), val("Bright", 1.0), val("Base", 0.1), val("Fossil", 0.0)
    uvn = nodes.new("ShaderNodeUVMap")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(uvn.outputs[0], sep.inputs[0])
    div = nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = spacing
    links.new(sep.outputs[0], div.inputs[0])
    sub = nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    links.new(div.outputs[0], sub.inputs[0])
    links.new(offset, sub.inputs[1])
    fr = nodes.new("ShaderNodeMath")
    fr.operation = "FRACT"
    links.new(sub.outputs[0], fr.inputs[0])
    ramp = nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.45, (0, 0, 0, 1)
    els[1].position, els[1].color = 0.93, (1, 1, 1, 1)
    e3 = els.new(1.0)
    e3.color = (0, 0, 0, 1)
    links.new(fr.outputs[0], ramp.inputs[0])
    mul = nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY_ADD"
    links.new(ramp.outputs[0], mul.inputs[0])
    links.new(bright, mul.inputs[1])
    links.new(base, mul.inputs[2])
    links.new(mul.outputs[0], p.inputs["Emission Strength"])
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    links.new(fos, mix.inputs[0])
    mix.inputs[6].default_value = lin(clean_hex)
    mix.inputs[7].default_value = lin(dirty_hex)
    links.new(mix.outputs[2], p.inputs["Emission Color"])
    return m, {"offset": offset, "bright": bright, "base": base, "fossil": fos}


# ---------------------------------------------------------------- world, floor, lights, compositor
keyseries(bg.inputs["Color"], "default_value", lambda f: mixc(lin(NIGHT), lin(DAY), daylight(f) if f < 404 else 0.4))

floor_mat = principled("Floor", FLOOR, rough=0.28, metal=0.0)
fp = bsdf(floor_mat)
fp.inputs["Specular IOR Level"].default_value = 0.6
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=400)
floor = mesh_obj("Floor", bm, floor_mat)
floor.location = (100, 0, 0)

# soft rim lights from behind, cool fill from the front
for i, (loc, energy, color) in enumerate(
    [((-12, 14, 9), 2500, "#6f9dff"), ((12, 14, 9), 2500, "#6fd8ff"), ((0, -16, 14), 600, "#9fb6d8")]
):
    ld = bpy.data.lights.new(f"Rim_{i}", "AREA")
    ld.energy = energy
    ld.size = 8
    ld.specular_factor = 0.12
    ld.color = lin(color)[:3]
    lo = link(bpy.data.objects.new(f"Rim_{i}", ld))
    lo.location = loc
    tc = lo.constraints.new("TRACK_TO")
    tc.target = empty(f"RimTarget_{i}", (0, 0, 0.5))
end_rim = []
for i, (loc, energy, color) in enumerate([((192, 10, 8), 1500, "#6f9dff"), ((208, 10, 8), 1500, "#6fd8ff"), ((200, -12, 10), 350, "#9fb6d8")]):
    ld = bpy.data.lights.new(f"EndRim_{i}", "AREA")
    ld.energy = energy
    ld.size = 7
    ld.specular_factor = 0.12
    ld.color = lin(color)[:3]
    lo = link(bpy.data.objects.new(f"EndRim_{i}", ld))
    lo.location = loc
    tc = lo.constraints.new("TRACK_TO")
    tc.target = empty(f"EndRimTarget_{i}", (200, 0, 0.5))

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.angle = math.radians(4)
sun_data.color = lin("#ffd9a8")[:3]
sun = link(bpy.data.objects.new("Sun", sun_data))
keyseries(sun_data, "energy", lambda f: 2.2 * daylight(f) if f < 404 else 0.0)
keyseries(
    sun,
    "rotation_euler",
    lambda f: (math.radians(90 - 62 * daylight(f)), 0.0, math.radians(-90 + (hour(f) - 6) * 15)),
)

ng = bpy.data.node_groups.new("Bloom", "CompositorNodeTree")
ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
scene.compositing_node_group = ng
rl = ng.nodes.new("CompositorNodeRLayers")
glare = ng.nodes.new("CompositorNodeGlare")
glare.inputs["Type"].default_value = "Bloom"
glare.inputs["Quality"].default_value = "High"
glare.inputs["Threshold"].default_value = 1.0
glare.inputs["Strength"].default_value = 0.85
glare.inputs["Size"].default_value = 0.8
gout = ng.nodes.new("NodeGroupOutput")
ng.links.new(rl.outputs["Image"], glare.inputs["Image"])
# soft vignette
ell = ng.nodes.new("CompositorNodeEllipseMask")
ell.inputs["Size"].default_value = (0.92, 0.82)
vblur = ng.nodes.new("CompositorNodeBlur")
vblur.inputs["Size"].default_value = (260, 260)
ng.links.new(ell.outputs["Mask"], vblur.inputs["Image"])
vmap = ng.nodes.new("ShaderNodeMath")
vmap.operation = "MULTIPLY_ADD"
vmap.inputs[1].default_value = 0.5
vmap.inputs[2].default_value = 0.5
ng.links.new(vblur.outputs["Image"], vmap.inputs[0])
vmul = ng.nodes.new("ShaderNodeMix")
vmul.data_type = "RGBA"
vmul.blend_type = "MULTIPLY"
vmul.inputs[0].default_value = 1.0
ng.links.new(glare.outputs[0], vmul.inputs[6])
ng.links.new(vmap.outputs[0], vmul.inputs[7])
ng.links.new(vmul.outputs[2], gout.inputs[0])

# ---------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 28
cam_data.clip_end = 400
cam = link(bpy.data.objects.new("Camera", cam_data))
scene.camera = cam
target = empty("CamTarget")
tc = cam.constraints.new("TRACK_TO")
tc.target = target
tc.track_axis = "TRACK_NEGATIVE_Z"
tc.up_axis = "UP_Y"
WIDE = ((0.9, -20.5, 12.0), (0.9, 0.8, 1.0))
CLOSE = ((-2.0, -16.0, 8.6), (-2.2, -0.8, 1.3))
END_CAM = ((200.0, -11.6, 6.6), (200.0, 0.5, 0.9))
CAM_KEYS = [
    (1, ((1.7, -21.5, 12.6), WIDE[1])),
    (128, ((0.9, -20.0, 11.7), WIDE[1])),
    (226, CLOSE),
    (240, ((-2.1, -15.6, 8.4), CLOSE[1])),
    (264, ((1.3, -20.6, 12.1), WIDE[1])),
    (403, ((0.3, -19.3, 11.2), WIDE[1])),
]


def cam_at(f):
    if f >= 404:
        t = (f - 404) / (END - 404)
        loc = Vector(END_CAM[0]) + Vector((0.0, 0.5, -0.2)) * t
        return tuple(loc), END_CAM[1]
    return keys_interp(CAM_KEYS, f)


for fr in range(1, END + 1):
    loc, tgt = cam_at(fr)
    key(cam, "location", fr, loc)
    key(target, "location", fr, tgt)

# ---------------------------------------------------------------- shared materials
metal = principled("Metal", METAL, rough=0.3, metal=0.7)
metal2 = principled("Metal2", METAL2, rough=0.35, metal=0.6)
glass_dark = principled("DarkGlass", "#0a1018", rough=0.08, metal=0.2)
label_mat, _ = flat("Label", TEXT, 0.85)
label_dim, _ = flat("LabelDim", MUTED, 0.9)

# ---------------------------------------------------------------- hub
HUB_Z = 2.15
HUB_R = 1.2


def core_material(name):
    """Sphere core: green emission below Level (object Z), smoky above. Controls: Level, Fossil."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    level = nodes.new("ShaderNodeValue")
    level.name = level.label = "Level"
    fos = nodes.new("ShaderNodeValue")
    fos.name = fos.label = "Fossil"
    tcn = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(tcn.outputs["Object"], sep.inputs[0])
    # fac = 1 below the level, 0 above
    d = nodes.new("ShaderNodeMath")
    d.operation = "SUBTRACT"
    links.new(level.outputs[0], d.inputs[0])
    links.new(sep.outputs[2], d.inputs[1])
    fac = nodes.new("ShaderNodeMapRange")
    fac.interpolation_type = "SMOOTHSTEP"
    fac.inputs["From Min"].default_value = -0.015
    fac.inputs["From Max"].default_value = 0.015
    links.new(d.outputs[0], fac.inputs["Value"])
    # bright meniscus line at the surface
    ab = nodes.new("ShaderNodeMath")
    ab.operation = "ABSOLUTE"
    links.new(d.outputs[0], ab.inputs[0])
    men = nodes.new("ShaderNodeMapRange")
    men.inputs["From Min"].default_value = 0.035
    men.inputs["From Max"].default_value = 0.0
    men.inputs["To Max"].default_value = 14.0
    links.new(ab.outputs[0], men.inputs["Value"])
    # empty part: dark, smoky red when fossil fills in
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0
    noise.inputs["Detail"].default_value = 4.0
    links.new(tcn.outputs["Object"], noise.inputs["Vector"])
    smoke_s = nodes.new("ShaderNodeMath")
    smoke_s.operation = "MULTIPLY"
    links.new(noise.outputs["Fac"], smoke_s.inputs[0])
    links.new(fos.outputs[0], smoke_s.inputs[1])
    smoke_k = nodes.new("ShaderNodeMath")
    smoke_k.operation = "MULTIPLY"
    smoke_k.inputs[1].default_value = 0.12
    links.new(smoke_s.outputs[0], smoke_k.inputs[0])
    dark = nodes.new("ShaderNodeEmission")
    dark.inputs["Color"].default_value = lin(DIRTY)
    links.new(smoke_k.outputs[0], dark.inputs["Strength"])
    dark_body = nodes.new("ShaderNodeBsdfPrincipled")
    dark_body.inputs["Base Color"].default_value = lin("#030406")
    dark_body.inputs["Roughness"].default_value = 0.65
    add_dark = nodes.new("ShaderNodeAddShader")
    links.new(dark_body.outputs[0], add_dark.inputs[0])
    links.new(dark.outputs[0], add_dark.inputs[1])
    green = nodes.new("ShaderNodeEmission")
    green.inputs["Color"].default_value = lin(CORE)
    green.inputs["Strength"].default_value = 4.5
    mixs = nodes.new("ShaderNodeMixShader")
    links.new(fac.outputs[0], mixs.inputs[0])
    links.new(add_dark.outputs[0], mixs.inputs[1])
    links.new(green.outputs[0], mixs.inputs[2])
    line = nodes.new("ShaderNodeEmission")
    line.inputs["Color"].default_value = lin("#c8ffe6")
    links.new(men.outputs[0], line.inputs["Strength"])
    add = nodes.new("ShaderNodeAddShader")
    links.new(mixs.outputs[0], add.inputs[0])
    links.new(line.outputs[0], add.inputs[1])
    links.new(add.outputs[0], out.inputs["Surface"])
    return m, level.outputs[0], fos.outputs[0]


def level_z(lv, r=HUB_R * 0.82):
    return -r * 1.06 + 2.12 * r * lv if lv > 0.005 else -r * 1.2


def build_hub(parent, core_mat, ring_mat):
    cylinder("HubBase", 1.45, 0.25, (0, 0, 0), metal2, parent)
    cylinder("HubPlinth", 1.05, 0.55, (0, 0, 0.25), metal, parent, r2=0.85)
    torus("HubBaseGlow", 1.42, 0.035, (0, 0, 0.27), ring_mat, parent)
    cylinder("HubNeck", 0.35, 0.6, (0, 0, 0.8), metal, parent)
    shell = principled("HubShell", "#0b1622", rough=0.04, metal=0.0, alpha=0.18)
    bsdf(shell).inputs["Coat Weight"].default_value = 1.0
    sphere("HubShell", HUB_R, (0, 0, HUB_Z), shell, parent, segs=48)
    sphere("HubCore", HUB_R * 0.82, (0, 0, HUB_Z), core_mat, parent, segs=48)
    # substation hint: insulator posts with glowing caps, and gimbal rings
    for k in range(8):
        a = k / 8 * 2 * math.pi + math.pi / 8
        x, y = 1.25 * math.cos(a), 1.25 * math.sin(a)
        for s in range(4):
            cylinder(f"Insul_{k}_{s}", 0.09, 0.06, (x, y, 0.27 + 0.12 * s), metal2, parent, segs=16)
            cylinder(f"InsulRod_{k}_{s}", 0.04, 0.12, (x, y, 0.27 + 0.12 * s), metal, parent, segs=12)
        sphere(f"InsulCap_{k}", 0.06, (x, y, 0.8), ring_mat, parent, segs=12)
    eq = torus("HubRing", HUB_R + 0.12, 0.03, (0, 0, HUB_Z), ring_mat, parent)
    r2 = torus("HubRingTilt", HUB_R + 0.2, 0.02, (0, 0, HUB_Z), metal2, parent)
    r2.rotation_euler = (math.radians(72), 0, math.radians(30))
    return eq, r2


hub_root = empty("Hub")
core_mat, core_level_sock, core_fossil_sock = core_material("HubCore")
hub_ring_mat = principled("HubRingGlow", "#0c1418", rough=0.3, emit=CLEAN, strength=2.0)
hub_ring_emit_col = bsdf(hub_ring_mat).inputs["Emission Color"]
hub_ring_emit = bsdf(hub_ring_mat).inputs["Emission Strength"]
ring_eq, ring_tilt = build_hub(hub_root, core_mat, hub_ring_mat)
keyseries(core_level_sock, "default_value", lambda f: level_z(core_level(f)))
keyseries(core_fossil_sock, "default_value", fossil)
keyseries(hub_ring_emit_col, "default_value", lambda f: mixc(lin(CLEAN), lin(DIRTY), fossil(f)))
keyseries(hub_ring_emit, "default_value", lambda f: 0.6 + 1.4 * core_level(f) + 0.6 * fossil(f))
for fr in (1, END):
    key(ring_tilt, "rotation_euler", fr, (math.radians(72), 0, math.radians(30 + 40 * (fr - 1) / FPS)))
text("The grid", 0.5, "sans_bold", label_mat, (0, 0, HUB_Z + HUB_R + 0.6), face=cam)

# ---------------------------------------------------------------- 24-hour dial
DIAL_R = 2.85


def ang(h):
    return math.radians(90 - 15 * h)


dial_mat, _ = emission_mat("Dial", "#5b7894", 0.9)
tick_major, _ = emission_mat("TickMajor", "#a9c3da", 1.6)
torus("DialRing", DIAL_R, 0.025, (0, 0, 0.03), dial_mat)
torus("DialRingInner", DIAL_R - 0.42, 0.012, (0, 0, 0.03), dial_mat)
for h in range(24):
    a = ang(h)
    major = h % 6 == 0
    ln = 0.34 if major else 0.18
    t = box(f"Tick_{h:02d}", ln, 0.05 if major else 0.035, 0.03, (0, 0, 0), tick_major if major else dial_mat, bevel=0)
    rr = DIAL_R - 0.04 - ln / 2
    t.location = (rr * math.cos(a), rr * math.sin(a), 0.02)
    t.rotation_euler = (0, 0, a)
for h, lab in ((0, "12 AM"), (6, "6 AM"), (12, "12 PM"), (18, "6 PM")):
    a = ang(h)
    rr = DIAL_R + 0.62
    text(lab, 0.34, "sans_bold", label_dim, (rr * math.cos(a), rr * math.sin(a), 0.25), face=cam, name=f"DialLabel_{h}")

# the clean window the clearinghouse spreads load across (10 AM - 4 PM)
win_mat, win_em = emission_mat("ClearWindow", CLEAN, 0.0)
torus("ClearWindowArc", DIAL_R - 0.21, 0.05, (0, 0, 0.04), win_mat, a0=ang(16), a1=ang(10), n=48)
key(win_em.inputs["Strength"], "default_value", 1, 0.0)
key(win_em.inputs["Strength"], "default_value", 262, 0.0)
key(win_em.inputs["Strength"], "default_value", 278, 2.2)
key(win_em.inputs["Strength"], "default_value", 404, 2.2)
key(win_em.inputs["Strength"], "default_value", 405, 0.0)

# clock hand + glowing hour bead (the sun on the dial)
hand = empty("HourHand", (0, 0, 0))
hand_mat, _ = emission_mat("Hand", WARM, 3.0)
box("HandBar", DIAL_R - 1.55, 0.04, 0.025, (1.55 + (DIAL_R - 1.55) / 2, 0, 0.03), hand_mat, hand, bevel=0)
sun_bead_mat, sun_bead_em = emission_mat("SunBead", WARM, 8.0)
sphere("SunBead", 0.16, (DIAL_R, 0, 0.18), sun_bead_mat, hand, segs=24)
keyseries(hand, "rotation_euler", lambda f: (0, 0, ang(hour(f))), range(1, END + 1))
keyseries(sun_bead_em.inputs["Color"], "default_value", lambda f: mixc(lin("#ff9a4a"), lin(WARM), daylight(f)))

# ---------------------------------------------------------------- sources
src_root = empty("Sources")

# wind farm
WIND = [(-8.2, 6.4), (-10.6, 4.9), (-6.0, 8.2)]
blade_mat = principled("Blade", "#c9d3dc", rough=0.35)
tower_mat = principled("Tower", "#8f9aa6", rough=0.4, metal=0.3)
nacelle_glow, _ = emission_mat("NacelleLight", "#ff3b3b", 3.0)
for w, (x, y) in enumerate(WIND):
    hgt = 4.6
    cylinder(f"Tower_{w}", 0.16, hgt, (x, y, 0), tower_mat, src_root, r2=0.08, segs=24)
    box(f"Nacelle_{w}", 0.32, 0.7, 0.3, (x, y + 0.1, hgt - 0.12), tower_mat, src_root, bevel=0.06)
    sphere(f"NacLight_{w}", 0.05, (x, y + 0.3, hgt + 0.2), nacelle_glow, src_root, segs=12)
    rotor = empty(f"Rotor_{w}", (x, y - 0.32, hgt + 0.03), parent=src_root)
    sphere(f"Spinner_{w}", 0.13, (0, 0, 0), tower_mat, rotor, segs=16)
    for b in range(3):
        bl = box(f"Blade_{w}_{b}", 0.16, 0.04, 2.1, (0, 0, 0), blade_mat, rotor, bevel=0.015)
        bl.rotation_euler = (0, b * 2 * math.pi / 3, 0)
    spd = 2 * math.pi * (0.32 + 0.04 * w)
    keyseries(rotor, "rotation_euler", lambda f, spd=spd, w=w: (0, spd * f / FPS + w, 0), range(1, END + 1, 4))

# solar farm
panel_mat = principled("Panel", "#0b1e3c", rough=0.12, metal=0.3, emit="#3a7bff", strength=0.0)
keyseries(bsdf(panel_mat).inputs["Emission Strength"], "default_value", lambda f: 0.05 + 0.5 * daylight(f))
frame_mat = principled("PanelFrame", "#7e8a96", rough=0.4, metal=0.6)
SOLAR_C = (-9.4, 0.8)
for r in range(3):
    for c in range(5):
        x = SOLAR_C[0] + (c - 2) * 1.05
        y = SOLAR_C[1] + (r - 1) * 1.15
        box(f"Post_{r}_{c}", 0.06, 0.06, 0.38, (x, y, 0), frame_mat, src_root, bevel=0)
        pnl = box(f"Panel_{r}_{c}", 0.96, 0.7, 0.04, (x, y, 0.38), panel_mat, src_root, bevel=0.01)
        pnl.rotation_euler = (math.radians(-28), 0, 0)
box("Inverter", 0.5, 0.4, 0.55, (-6.8, 0.8, 0), metal2, src_root)
box("WindJunction", 0.5, 0.4, 0.55, (-6.4, 5.2, 0), metal2, src_root)

# gas & coal plant
PLANT = (-8.4, -4.7)
plant_root = empty("Plant", (*PLANT, 0), parent=src_root)


def build_plant(parent, glow_mat):
    box("PlantHall", 2.6, 1.4, 1.2, (0.3, 0.45, 0), metal2, parent, bevel=0.05)
    box("PlantHall2", 1.3, 1.1, 0.75, (1.6, -0.2, 0), metal, parent, bevel=0.05)
    stacks = [(-0.55, 0.0, 3.4), (0.4, 0.0, 3.0)]
    for s, (x, y, h) in enumerate(stacks):
        cylinder(f"Stack_{s}", 0.24, h, (x, y, 0), metal2, parent, r2=0.19)
        cylinder(f"StackGlow_{s}", 0.205, 0.18, (x, y, h - 0.25), glow_mat, parent)
        torus(f"StackLip_{s}", 0.2, 0.03, (x, y, h), glow_mat, parent, n=32, m=8)
    # hyperbolic cooling tower
    bm = bmesh.new()
    prof = [(1.0, 0.0), (0.82, 0.6), (0.62, 1.4), (0.58, 1.8), (0.66, 2.2)]
    rings = []
    for rr, zz in prof:
        ring = [bm.verts.new((rr * math.cos(2 * math.pi * k / 32), rr * math.sin(2 * math.pi * k / 32), zz)) for k in range(32)]
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        for k in range(32):
            bm.faces.new((a[k], a[(k + 1) % 32], b[(k + 1) % 32], b[k]))
    ct = mesh_obj("CoolingTower", bm, metal, parent, smooth=True, loc=(-1.7, 0.9, 0))
    sol = ct.modifiers.new("Solid", "SOLIDIFY")
    sol.thickness = 0.04
    return stacks


plant_glow = principled("PlantGlow", "#140806", rough=0.5, emit=FIRE, strength=0.0)
plant_glow_s = bsdf(plant_glow).inputs["Emission Strength"]
keyseries(plant_glow_s, "default_value", lambda f: 9.0 * plant(f) if f < 404 else 0.0)
stacks = build_plant(plant_root, plant_glow)
fire_light_d = bpy.data.lights.new("PlantFire", "POINT")
fire_light_d.color = lin(FIRE)[:3]
fire_light_d.shadow_soft_size = 1.0
fire_light_d.specular_factor = 0.15
fire_light = link(bpy.data.objects.new("PlantFire", fire_light_d), plant_root)
fire_light.location = (0, -1.0, 1.6)
keyseries(fire_light_d, "energy", lambda f: 350 * plant(f) if f < 404 else 0.0)


SMOKE_TEX = bpy.data.textures.new("SmokeClouds", "CLOUDS")
SMOKE_TEX.noise_scale = 0.35


def smoke(parent, stacks, mat, f_start, f_end, every=7, life=44, seed=0):
    puffs = []
    for s, (x, y, h) in enumerate(stacks):
        for n_p, f0 in enumerate(range(f_start + 3 * s, f_end, every)):
            p = sphere(f"Puff_{seed}_{s}_{n_p:02d}", 0.5, (x, y, h), mat, parent, segs=24)
            dm = p.modifiers.new("Billow", "DISPLACE")
            dm.texture = SMOKE_TEX
            dm.texture_coords = "GLOBAL"
            dm.strength = 0.35
            dm.mid_level = 0.5
            drift = 0.35 + 0.12 * ((n_p + s) % 3)
            key(p, "scale", 1, (0, 0, 0))
            key(p, "scale", f0 - 1, (0, 0, 0))
            key(p, "scale", f0, (0.22, 0.22, 0.22))
            key(p, "location", f0, (x, y, h + 0.05))
            key(p, "scale", f0 + life * 2 // 3, (0.62, 0.62, 0.62))
            key(p, "location", f0 + life * 2 // 3, (x + drift, y + 0.25, h + 1.6))
            key(p, "scale", f0 + life, (0, 0, 0))
            key(p, "location", f0 + life, (x + drift * 1.6, y + 0.4, h + 2.4))
            puffs.append(p)
    return puffs


smoke_mat = principled("Smoke", SMOKE, rough=1.0, alpha=0.6, emit=FIRE, strength=0.0)
keyseries(bsdf(smoke_mat).inputs["Emission Strength"], "default_value", lambda f: 0.03 * plant(f))
smoke(plant_root, stacks, smoke_mat, 166, 216, every=6, life=42)

# ---------------------------------------------------------------- companies
comp_root = empty("Companies")
activity = []  # per company: list of (socket, fn(load))


def build_ev(parent, accent_mat, car_mats):
    box("CanopyRoof", 3.0, 1.7, 0.12, (0, 0, 1.25), metal2, parent, bevel=0.03)
    box("CanopyStrip", 3.0, 0.06, 0.05, (0, -0.86, 1.28), accent_mat, parent, bevel=0)
    for px in (-1.35, 1.35):
        for py in (-0.7, 0.7):
            box(f"CanopyPost_{px}_{py}", 0.08, 0.08, 1.25, (px, py, 0), metal, parent, bevel=0)
    for k in range(3):
        x = (k - 1) * 0.95
        box(f"Charger_{k}", 0.24, 0.16, 0.75, (x, 0.55, 0), metal, parent, bevel=0.02)
        box(f"ChargerScreen_{k}", 0.16, 0.02, 0.22, (x, 0.465, 0.42), accent_mat, parent, bevel=0)
        car = car_mats[k % len(car_mats)]
        box(f"CarBody_{k}", 0.62, 1.1, 0.26, (x, -0.2, 0.08), car, parent, bevel=0.08)
        box(f"CarCabin_{k}", 0.52, 0.6, 0.2, (x, -0.12, 0.33), glass_dark, parent, bevel=0.07)
        for wx in (-0.3, 0.3):
            for wy in (-0.55, 0.15):
                c = cylinder(f"Wheel_{k}_{wx}_{wy}", 0.11, 0.08, (x + wx - 0.04 * (wx > 0), -0.2 + wy + 0.2, 0.11), metal, parent, segs=16)
                c.rotation_euler = (0, math.radians(90), 0)


def build_datacenter(parent, lights_mat):
    box("DCHall", 2.6, 1.7, 1.15, (0, 0, 0), metal2, parent, bevel=0.04)
    box("DCRoof", 2.7, 1.8, 0.08, (0, 0, 1.15), metal, parent, bevel=0.02)
    box("DCRacks", 2.2, 0.03, 0.85, (0, -0.86, 0.15), lights_mat, parent, bevel=0)
    box("DCRacksSide", 0.03, 1.3, 0.85, (-1.31, 0, 0.15), lights_mat, parent, bevel=0)
    for k in range(4):
        box(f"DCChiller_{k}", 0.45, 0.45, 0.25, (-0.9 + 0.6 * k, 0.3, 1.23), metal, parent, bevel=0.03)


def build_office(parent, windows_mat, accent_mat):
    box("Tower", 1.5, 1.5, 4.6, (0, 0, 0), windows_mat, parent, bevel=0.02)
    box("TowerCap", 1.6, 1.6, 0.12, (0, 0, 4.6), metal2, parent, bevel=0.02)
    box("HVAC", 0.8, 0.6, 0.3, (0.1, 0.1, 4.72), metal, parent, bevel=0.03)
    box("HVACGlow", 0.62, 0.02, 0.12, (0.1, -0.21, 4.8), accent_mat, parent, bevel=0)


def build_cold(parent, trim_mat):
    box("Warehouse", 3.0, 1.9, 1.0, (0, 0, 0), principled("WarehouseWall", "#1a2532", rough=0.45, metal=0.3), parent, bevel=0.04)
    box("WarehouseRoof", 3.1, 2.0, 0.08, (0, 0, 1.0), metal, parent, bevel=0.02)
    box("WarehouseTrim", 3.02, 0.03, 0.06, (0, -0.96, 0.85), trim_mat, parent, bevel=0)
    for k in range(4):
        x = -1.05 + 0.7 * k
        box(f"Dock_{k}", 0.5, 0.05, 0.6, (x, -0.95, 0), metal, parent, bevel=0)
        box(f"DockLight_{k}", 0.4, 0.02, 0.04, (x, -0.98, 0.64), trim_mat, parent, bevel=0)
    for k in range(2):
        box(f"Condenser_{k}", 0.6, 0.5, 0.3, (-0.6 + 1.2 * k, 0.3, 1.08), metal2, parent, bevel=0.03)


def blink_material(name, hexc):
    """Server lights: a grid of cells switched by 4D white noise stepped over time."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nodes, links = nt.nodes, nt.links
    p = bsdf(m)
    p.inputs["Base Color"].default_value = lin("#05070a")
    p.inputs["Roughness"].default_value = 0.2
    p.inputs["Emission Color"].default_value = lin(hexc)
    tcn = nodes.new("ShaderNodeTexCoord")
    sc = nodes.new("ShaderNodeVectorMath")
    sc.operation = "MULTIPLY"
    sc.inputs[1].default_value = (9.0, 9.0, 7.0)
    links.new(tcn.outputs["Object"], sc.inputs[0])
    fl = nodes.new("ShaderNodeVectorMath")
    fl.operation = "FLOOR"
    links.new(sc.outputs[0], fl.inputs[0])
    t = nodes.new("ShaderNodeValue")
    t.name = t.label = "Time"
    tfl = nodes.new("ShaderNodeMath")
    tfl.operation = "FLOOR"
    links.new(t.outputs[0], tfl.inputs[0])
    wn = nodes.new("ShaderNodeTexWhiteNoise")
    wn.noise_dimensions = "4D"
    links.new(fl.outputs[0], wn.inputs["Vector"])
    links.new(tfl.outputs[0], wn.inputs["W"])
    thr = nodes.new("ShaderNodeValue")
    thr.name = thr.label = "Activity"
    gt = nodes.new("ShaderNodeMath")
    gt.operation = "LESS_THAN"
    links.new(wn.outputs["Value"], gt.inputs[0])
    links.new(thr.outputs[0], gt.inputs[1])
    fr = nodes.new("ShaderNodeVectorMath")
    fr.operation = "FRACTION"
    links.new(sc.outputs[0], fr.inputs[0])
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(fr.outputs[0], sep.inputs[0])
    mask = None
    for comp in (0, 1, 2):
        lt = nodes.new("ShaderNodeMath")
        lt.operation = "LESS_THAN"
        lt.inputs[1].default_value = 0.72 if comp != 2 else 0.45
        links.new(sep.outputs[comp], lt.inputs[0])
        if mask is None:
            mask = lt
        else:
            mm = nodes.new("ShaderNodeMath")
            mm.operation = "MULTIPLY"
            links.new(mask.outputs[0], mm.inputs[0])
            links.new(lt.outputs[0], mm.inputs[1])
            mask = mm
    on = nodes.new("ShaderNodeMath")
    on.operation = "MULTIPLY"
    links.new(mask.outputs[0], on.inputs[0])
    links.new(gt.outputs[0], on.inputs[1])
    st = nodes.new("ShaderNodeMath")
    st.operation = "MULTIPLY"
    st.inputs[1].default_value = 6.0
    links.new(on.outputs[0], st.inputs[0])
    links.new(st.outputs[0], p.inputs["Emission Strength"])
    return m, t.outputs[0], thr.outputs[0]


def windows_material(name, hexc):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nodes, links = nt.nodes, nt.links
    p = bsdf(m)
    p.inputs["Base Color"].default_value = lin("#0a1220")
    p.inputs["Roughness"].default_value = 0.1
    p.inputs["Metallic"].default_value = 0.5
    p.inputs["Emission Color"].default_value = lin(hexc)
    tcn = nodes.new("ShaderNodeTexCoord")
    sc = nodes.new("ShaderNodeVectorMath")
    sc.operation = "MULTIPLY"
    sc.inputs[1].default_value = (5.0, 5.0, 3.2)
    links.new(tcn.outputs["Object"], sc.inputs[0])
    fr = nodes.new("ShaderNodeVectorMath")
    fr.operation = "FRACTION"
    links.new(sc.outputs[0], fr.inputs[0])
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(fr.outputs[0], sep.inputs[0])
    band = nodes.new("ShaderNodeMath")
    band.operation = "LESS_THAN"
    band.inputs[1].default_value = 0.55
    links.new(sep.outputs[2], band.inputs[0])
    act = nodes.new("ShaderNodeValue")
    act.name = act.label = "Activity"
    st = nodes.new("ShaderNodeMath")
    st.operation = "MULTIPLY"
    links.new(band.outputs[0], st.inputs[0])
    links.new(act.outputs[0], st.inputs[1])
    links.new(st.outputs[0], p.inputs["Emission Strength"])
    return m, act.outputs[0]


COMP_POS = [(9.2, 2.0), (9.2, -2.0), (8.2, 6.2), (8.0, -6.0)]
comp_roots = []
for c, (name, hexc) in enumerate(COMPANIES):
    root = empty(f"Company_{c}", (*COMP_POS[c], 0), parent=comp_root)
    comp_roots.append(root)
    accent = principled(f"Accent_{c}", "#0b0f14", rough=0.3, emit=hexc, strength=1.0)
    acc_s = bsdf(accent).inputs["Emission Strength"]
    keyseries(acc_s, "default_value", lambda f, c=c: 0.4 + 5.0 * load(c, f))
    if c == 0:
        cars = [principled(f"Car_{k}", col, rough=0.18, metal=0.6) for k, col in enumerate(("#d9dde2", "#1c2430", "#7a1f24"))]
        build_ev(root, accent, cars)
        root.rotation_euler = (0, 0, math.radians(-90))
    elif c == 1:
        lm, t_sock, act_sock = blink_material("ServerLights", hexc)
        keyseries(t_sock, "default_value", lambda f: f / 3.0, range(1, END + 1, 1))
        keyseries(act_sock, "default_value", lambda f: 0.15 + 0.6 * load(1, f))
        build_datacenter(root, lm)
        root.rotation_euler = (0, 0, math.radians(-90))
    elif c == 2:
        wm, act_sock = windows_material("OfficeWindows", "#9ad6ff")
        keyseries(act_sock, "default_value", lambda f: 0.3 + 1.5 * load(2, f))
        build_office(root, wm, accent)
    else:
        build_cold(root, accent)
        root.rotation_euler = (0, 0, math.radians(-90))
    lab_off = {0: (0.0, 0.0, 2.0), 1: (0.0, 0.0, 1.9), 2: (0.0, 0.0, 5.55), 3: (0.0, 0.0, 1.75)}[c]
    lab_mat, _ = flat(f"CompLabel_{c}", hexc, 0.9)
    text(name, 0.42, "sans_bold", lab_mat, (COMP_POS[c][0] + lab_off[0], COMP_POS[c][1] + lab_off[1], lab_off[2]), face=cam, name=f"CompLabel_{c}")

text("Solar", 0.5, "sans_bold", label_mat, (SOLAR_C[0], SOLAR_C[1] + 0.4, 1.45), face=cam)
text("Wind", 0.5, "sans_bold", label_mat, (-8.4, 5.4, 2.5), face=cam)
text("Gas & coal", 0.5, "sans_bold", label_mat, (PLANT[0] + 0.4, PLANT[1] - 1.5, 0.25), face=cam)

# ---------------------------------------------------------------- wires
HUB_PORT_R = 1.42
HUB_PORT_Z = 0.3


def hub_port(p):
    d = Vector((p[0], p[1], 0)).normalized()
    return d * HUB_PORT_R + Vector((0, 0, HUB_PORT_Z)), d


def wire_ctrl(src, dst_hub=True):
    """Cubic Bezier from an outer point to the hub (or reverse), entering the hub radially."""
    outer = Vector((src[0], src[1], 0.14))
    port, d = hub_port(src)
    p_in = port + d * 2.2
    p_in.z = 0.14
    p_out = outer + (Vector((0, 0, 0.14)) - outer) * 0.3
    p_out.y = outer.y
    if dst_hub:
        return (outer, p_out, p_in, port)
    return (port, p_in, p_out, outer)


wires = []


def make_wire(name, ctrl, clean_hex, dirty_hex, r=0.075, spacing=1.6, parent=None):
    m, ctl = wire_material(name, clean_hex, dirty_hex, spacing)
    ob, length = tube(name, ctrl, r, m, parent)
    return ob, ctl, length


# solar -> hub
_, ctl, _ = make_wire("Wire_Solar", wire_ctrl((-6.55, 0.8)), CLEAN, CLEAN)
offs = flow_speed(0.0, 0, lambda f: 0.25 + 1.3 * daylight(f))
keyseries(ctl["offset"], "default_value", lambda f: offs[f])
keyseries(ctl["bright"], "default_value", lambda f: 0.2 + 7.0 * daylight(f))
keyseries(ctl["base"], "default_value", lambda f: 0.05 + 0.5 * daylight(f))
# wind -> hub
_, ctl, _ = make_wire("Wire_Wind", wire_ctrl((-6.15, 5.2)), CLEAN, CLEAN)
offs = flow_speed(0.3, 0, lambda f: 0.9)
keyseries(ctl["offset"], "default_value", lambda f: offs[f])
key(ctl["bright"], "default_value", 1, 5.0)
key(ctl["base"], "default_value", 1, 0.35)
# plant -> hub
_, ctl, _ = make_wire("Wire_Plant", wire_ctrl((PLANT[0] + 2.3, PLANT[1] - 0.2)), DIRTY, DIRTY, r=0.085)
offs = flow_speed(0.0, 0, lambda f: 0.15 + 1.6 * plant(f))
keyseries(ctl["offset"], "default_value", lambda f: offs[f])
keyseries(ctl["bright"], "default_value", lambda f: 0.15 + 3.8 * plant(f))
keyseries(ctl["base"], "default_value", lambda f: 0.02 + 0.5 * plant(f))
key(ctl["fossil"], "default_value", 1, 1.0)

# hub -> companies
COMP_PORT = [(7.6, 2.0), (7.75, -2.0), (7.2, 5.4), (6.5, -6.0)]
for c, (name, hexc) in enumerate(COMPANIES):
    _, ctl, _ = make_wire(f"Wire_{name}", wire_ctrl(COMP_PORT[c], dst_hub=False), CLEAN, DIRTY)
    offs = flow_speed(0.25 * c, 0, lambda f, c=c: 0.2 + 2.2 * load(c, f))
    keyseries(ctl["offset"], "default_value", lambda f: offs[f])
    keyseries(ctl["bright"], "default_value", lambda f, c=c: (0.25 + 8.0 * (load(c, f) - 0.06)) * (1 - 0.8 * fossil(f)))
    keyseries(ctl["base"], "default_value", lambda f, c=c: (0.03 + 0.9 * (load(c, f) - 0.06)) * (1 - 0.6 * fossil(f)))
    keyseries(ctl["fossil"], "default_value", fossil)

# ---------------------------------------------------------------- "pick an hour" beads
BEAD_Z = 0.22


def dial_point(h, r=DIAL_R - 0.21, z=BEAD_Z):
    a = ang(h)
    return Vector((r * math.cos(a), r * math.sin(a), z))


for c, (name, hexc) in enumerate(COMPANIES):
    bm_mat, bm_em = emission_mat(f"Bead_{c}", hexc, 6.0)
    bead = sphere(f"Bead_{c}", 0.15, (0, 0, 0), bm_mat, segs=20)
    home = Vector((*COMP_PORT[c], 1.4))
    herd_pt = dial_point(13.0) + Vector((0, 0, 0.32 * c))
    pick_pt = dial_point(PICK[c])

    def path(a, b, t, lift):
        p = a.lerp(b, t)
        p.z += lift * math.sin(math.pi * t)
        return p

    def bead_at(f, c=c, home=home, herd_pt=herd_pt, pick_pt=pick_pt):
        if f < 82 + 5 * c:
            return home, 0.0
        if f < 118 + 5 * c:
            t = ss(82 + 5 * c, 118 + 5 * c, f)
            return path(home, herd_pt, t, 2.5), 1.0
        if f < 244:
            return herd_pt, 1.0
        if f < 262:
            return herd_pt, 1.0
        if f < 292 + 4 * c:
            t = ss(262 + 4 * c, 292 + 4 * c, f)
            return path(herd_pt, pick_pt, t, 1.2), 1.0
        if f < 404:
            return pick_pt, 1.0
        return pick_pt, 0.0

    for fr in range(1, END + 1, 1):
        p, vis = bead_at(fr)
        key(bead, "location", fr, tuple(p))
        s = 1.0 * vis
        key(bead, "scale", fr, (s, s, s))
    keyseries(bm_em.inputs["Strength"], "default_value", lambda f, c=c: 2.5 + 3.0 * bump(f, 126, 146, 4) + 2.5 * load(c, f) * (f > 262))

# ---------------------------------------------------------------- captions (camera space)
# lens 28, 4 units in front of the camera: the frame spans 5.14 x 2.89 units.
D = -4.0
shade_mat = bpy.data.materials.new("CaptionShade")
shade_mat.use_nodes = True
nt = shade_mat.node_tree
nt.nodes.clear()
so = nt.nodes.new("ShaderNodeOutputMaterial")
st_ = nt.nodes.new("ShaderNodeBsdfTransparent")
se = nt.nodes.new("ShaderNodeEmission")
se.inputs["Color"].default_value = (0, 0, 0, 1)
suv = nt.nodes.new("ShaderNodeTexCoord")
ssep = nt.nodes.new("ShaderNodeSeparateXYZ")
nt.links.new(suv.outputs["UV"], ssep.inputs[0])
spow = nt.nodes.new("ShaderNodeMapRange")
spow.inputs["To Min"].default_value = 0.75
spow.inputs["To Max"].default_value = 0.0
nt.links.new(ssep.outputs[1], spow.inputs["Value"])
smix = nt.nodes.new("ShaderNodeMixShader")
nt.links.new(spow.outputs[0], smix.inputs[0])
nt.links.new(st_.outputs[0], smix.inputs[1])
nt.links.new(se.outputs[0], smix.inputs[2])
nt.links.new(smix.outputs[0], so.inputs["Surface"])
shade_mat.surface_render_method = "BLENDED"
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
uvl = bm.loops.layers.uv.new("UVMap")
for f_ in bm.faces:
    for lp in f_.loops:
        lp[uvl].uv = (lp.vert.co.x + 0.5, lp.vert.co.y + 0.5)
shade = mesh_obj("CaptionShade", bm, shade_mat, cam)
shade.location = (0, -1.1, D + 0.05)
shade.scale = (5.6, 0.76, 1)

fader_mat, fader_op = flat("FadeToBlack", "#000000", 1.0)
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=3.0)
fader = mesh_obj("FadeToBlack", bm, fader_mat, cam)
fader.location = (0, 0, D + 0.3)
key(fader_op, "default_value", 1, 1.0)
key(fader_op, "default_value", 10, 0.0)
key(fader_op, "default_value", 396, 0.0)
key(fader_op, "default_value", 403, 1.0)
key(fader_op, "default_value", 406, 1.0)
key(fader_op, "default_value", 415, 0.0)

CAP_Y = -1.07
CAPTIONS = [
    (1, 74, "Four companies each have flexible load to schedule for tomorrow."),
    (78, 156, "Their carbon-aware tools all pick the same “cleanest” hour."),
    (160, 240, "The spare clean energy runs out, so fossil plants cover the rush."),
    (262, 400, "The clearinghouse schedules all the bids together."),
    (412, None, "Each company is paid its Shapley share of the carbon\nthat coordination avoided."),
]
for n_cap, (f_in, f_out, body) in enumerate(CAPTIONS):
    m, op = flat(f"Caption_{n_cap}", "#e3e9ee", 0.9)
    y = CAP_Y if "\n" not in body else CAP_Y + 0.02
    text(body, 0.105, "sans", m, (0, y, D), parent=cam, name=f"Caption_{n_cap}")
    fade(op, f_in, f_out, d=8)

KICKERS = [(1, 240, "Acting alone", "#ff8a6a"), (243, 262, "Rewind", WHITE), (264, 400, "Scheduled together", CLEAN)]
for n_k, (f_in, f_out, body, col) in enumerate(KICKERS):
    m, op = flat(f"Kicker_{n_k}", col, 0.9)
    text(body, 0.11, "serif", m, (-2.25, 1.17, D), align="LEFT", parent=cam, name=f"Kicker_{n_k}")
    fade(op, f_in, f_out, d=8)

# ---------------------------------------------------------------- end card (split frame, far off at x=200)
END_X = 200.0
end_root = empty("EndCard", (END_X, 0, 0))


def end_side(name, x, herded):
    root = empty(f"End_{name}", (x, 0.6, 0), scale=0.62, parent=end_root)
    cm, lvl, fos = core_material(f"EndCore_{name}")
    lvl.default_value = level_z(0.0 if herded else 0.72)
    fos.default_value = 1.0 if herded else 0.0
    rm = principled(f"EndRing_{name}", "#0c1418", rough=0.3, emit=DIRTY if herded else CLEAN, strength=1.2 if herded else 2.0)
    build_hub(root, cm, rm)
    pr = empty(f"EndPlant_{name}", (-4.6, 2.2, 0), parent=root)
    pg = principled(f"EndPlantGlow_{name}", "#140806", rough=0.5, emit=FIRE, strength=9.0 if herded else 0.0)
    st = build_plant(pr, pg)
    if herded:
        sm = principled(f"EndSmoke_{name}", SMOKE, rough=1.0, alpha=0.6, emit=FIRE, strength=0.03)
        smoke(pr, st, sm, 380, 476, every=6, life=42, seed=1)
        fl_d = bpy.data.lights.new(f"EndFire_{name}", "POINT")
        fl_d.color = lin(FIRE)[:3]
        fl_d.energy = 200
        fl_d.specular_factor = 0.15
        fl = link(bpy.data.objects.new(f"EndFire_{name}", fl_d), pr)
        fl.location = (0, -0.8, 2.6)
    # wire plant -> hub
    m, ctl = wire_material(f"EndWirePlant_{name}", DIRTY, DIRTY)
    ctrl = (Vector((-3.0, 2.0, 0.14)), Vector((-2.4, 1.2, 0.14)), Vector((-2.2, 0.8, 0.14)), Vector((-1.2, 0.7, 0.3)))
    tube(f"EndWirePlant_{name}", ctrl, 0.085, m, root)
    ctl["fossil"].default_value = 1.0
    ctl["bright"].default_value = 3.8 if herded else 0.1
    ctl["base"].default_value = 0.5 if herded else 0.02
    for fr in (380, END):
        key(ctl["offset"], "default_value", fr, (fr - 380) / FPS * (1.6 if herded else 0.1))
    # solar -> hub
    m, ctl = wire_material(f"EndWireSolar_{name}", CLEAN, CLEAN)
    ctrl = (Vector((-3.4, -1.4, 0.14)), Vector((-2.4, -1.4, 0.14)), Vector((-2.0, -0.6, 0.14)), Vector((-1.25, -0.5, 0.3)))
    tube(f"EndWireSolar_{name}", ctrl, 0.075, m, root)
    ctl["bright"].default_value = 6.0
    ctl["base"].default_value = 0.4
    for fr in (380, END):
        key(ctl["offset"], "default_value", fr, (fr - 380) / FPS * 1.2)
    for r_ in range(2):
        for c_ in range(3):
            pn = box(f"EndPanel_{name}_{r_}_{c_}", 0.9, 0.65, 0.04, (-4.6 + c_ * 0.98, -1.0 - r_ * 0.95, 0.35), panel_mat, root, bevel=0.01)
            pn.rotation_euler = (math.radians(-28), 0, 0)
    # four company wires and markers
    for c, (cname, hexc) in enumerate(COMPANIES):
        y = 2.1 - c * 1.4
        m, ctl = wire_material(f"EndWire_{name}_{c}", CLEAN, DIRTY)
        port = Vector((1.25, 0.0, 0.3))
        a = math.atan2(y * 0.5, 2.0)
        port = Vector((1.42 * math.cos(a), 1.42 * math.sin(a), 0.3))
        ctrl = (port, port + Vector((math.cos(a), math.sin(a), 0)) * 1.4 + Vector((0, 0, -0.16)), Vector((2.6, y, 0.14)), Vector((3.6, y, 0.14)))
        tube(f"EndWire_{name}_{c}", ctrl, 0.07, m, root)
        ctl["fossil"].default_value = 1.0 if herded else 0.0
        ctl["bright"].default_value = 2.2 if herded else (7.0 if c in (1, 2) else 0.25)
        ctl["base"].default_value = 0.35 if herded else (0.8 if c in (1, 2) else 0.03)
        spd = 2.0 if herded else (2.0 if c in (1, 2) else 0.25)
        for fr in (380, END):
            key(ctl["offset"], "default_value", fr, 0.3 * c + (fr - 380) / FPS * spd)
        cm_ = principled(f"EndChip_{name}_{c}", "#0b0f14", rough=0.3, emit=hexc, strength=3.0)
        box(f"EndChip_{name}_{c}", 0.55, 0.55, 0.55, (3.95, y, 0), cm_, root, bevel=0.06)


end_side("Herded", -4.1, True)
end_side("Coordinated", 4.1, False)
div_mat, _ = emission_mat("EndDivider", "#3a4a5c", 1.2)
div = box("EndDivider", 0.012, 0.012, 1.5, (0, 0.62, D), div_mat, cam, bevel=0)
div.rotation_euler = (math.radians(90), 0, 0)

title_mat, title_op = flat("EndTitle", WHITE, 0.95)
text("Carbon Clearinghouse", 0.22, "serif", title_mat, (0, 1.06, D), parent=cam, name="EndTitle")
fade(title_op, 410, None, d=12)
for n_s, (body, col, x) in enumerate((("Same “cleanest” hour", "#ff7a5a", -1.2), ("Scheduled together", CLEAN, 1.2))):
    m, op = flat(f"EndSide_{n_s}", col, 0.95)
    text(body, 0.12, "sans_bold", m, (x, 0.74, D), parent=cam, name=f"EndSide_{n_s}")
    fade(op, 412, None, d=10)
# divider only on the end card
for fr, v in ((1, 0.0), (404, 0.0), (405, 1.0)):
    key(div, "scale", fr, (1, 1, v if v else 0.0001))

# ---------------------------------------------------------------- output
scene.render.image_settings.media_type = "VIDEO"
scene.render.image_settings.file_format = "FFMPEG"
scene.render.ffmpeg.format = "MPEG4"
scene.render.ffmpeg.codec = "H264"
scene.render.ffmpeg.constant_rate_factor = "HIGH"
scene.render.ffmpeg.ffmpeg_preset = "GOOD"
scene.render.filepath = os.path.join(HERE, "grid_hub_")
scene.frame_set(1)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "grid_hub.blend"))
print("BUILD OK", len(bpy.data.objects), "objects")
