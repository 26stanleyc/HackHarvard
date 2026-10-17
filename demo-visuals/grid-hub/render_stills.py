"""Render key-moment stills: Blender -b grid_hub.blend --python render_stills.py -- 40 140 ..."""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(bpy.data.filepath))
args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
pct = 50
frames = []
for a in args:
    if a.startswith("pct="):
        pct = int(a[4:])
    else:
        frames.append(int(a))
scene = bpy.context.scene
scene.render.resolution_percentage = pct
scene.render.image_settings.media_type = "IMAGE"
scene.render.image_settings.file_format = "PNG"
os.makedirs(os.path.join(HERE, "stills"), exist_ok=True)
for f in frames:
    scene.frame_set(f)
    scene.render.filepath = os.path.join(HERE, "stills", f"frame_{f:03d}.png")
    bpy.ops.render.render(write_still=True)
