# SPDX-License-Identifier: GPL-3.0-or-later
"""Send to Cariquima: a one-click bridge from Blender.

Maya sends a mesh to Substance with one button. Without this, getting a mesh
into Cariquima takes six steps: export, pick a folder, pick a format, remember
to mark the high-poly, switch programs, import. With this it takes one.

HOW THE TWO SIDES TALK. Through a shared folder, not a socket. A local server
would be more immediate, but it opens a port on the user's machine: Windows
asks about the firewall the first time, a studio's antivirus sometimes answers
for it, and with Cariquima closed the send is lost. The folder asks nothing,
waits for Cariquima to open, and can be inspected in a file browser when
something goes wrong.

IT IS WRITTEN AS `.tmp` AND THEN RENAMED. A 400 MB send takes a while; if
Cariquima looked at the folder halfway through it would find a GLB that exists,
has a size, and is garbage. Renaming a folder is atomic on all three systems,
so a send is either complete or absent.

WHAT IS CHECKED BEFORE SENDING. Everything that can be known here and would
cost a bake to discover over there. A mesh without UVs cannot be painted, so
that blocks; everything else warns. The panel says it before the button is
pressed, because a send that arrives broken reads as a Cariquima bug.

Install: drag the release ZIP onto Blender, or Edit > Preferences > Get
Extensions > Install from Disk. It shows up in the 3D View sidebar (N key),
"Cariquima" tab.
"""

# Read only by Blender versions older than 4.2, which install legacy add-ons.
# From 4.2 on, `blender_manifest.toml` is the source of truth.
bl_info = {
    "name": "Send to Cariquima",
    "author": "dannybend",
    "version": (1, 2, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Cariquima",
    "description": "Send the selected low-poly and high-poly to Cariquima to paint them",
    "doc_url": "https://github.com/dannybend/cariquima-blender-bridge",
    "category": "Import-Export",
}

import json
import os
import shutil
import subprocess
import sys
import time

import bpy

# Must match `BlenderBridge.VERSION` on the Cariquima side. The add-on and the
# application update separately, so the version travels with every send, and a
# side that does not understand it says so instead of guessing.
CONTRACT_VERSION = 1
# Name of the heartbeat file Cariquima writes while it runs. Mirrors
# `BlenderBridge.HEARTBEAT`.
HEARTBEAT = "running.lock"
# Older than this and Cariquima is considered closed. It writes every five
# seconds; four times that leaves room for a long operation without declaring
# a running application dead.
HEARTBEAT_STALE = 20.0
# Above this, a "low-poly" is not a low-poly. It warns, it does not block:
# there are legitimate workflows with dense meshes, and deciding for the user
# would be worse.
LOW_POLY_WARN_TRIS = 500_000
# The name preferences are registered under. `__package__` and not
# `__name__`: installed as an extension, the module is
# `bl_ext.<repo>.cariquima_bridge`. Run as a loose script there is no package.
ADDON_ID = __package__ or __name__
# Where to get Cariquima. Shown when the executable is not set.
CARIQUIMA_URL = "https://dannybend.itch.io/cariquima-3d-texturing-software"

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
_ICONS = {
    ERROR: "ERROR",
    WARN: "ERROR",  # Blender has no warning icon distinct from the error one.
    INFO: "INFO",
}

# The heartbeat is read on every panel redraw, and Blender redraws the sidebar
# when the mouse moves. Without a cache that would be hundreds of reads per
# second for an answer that changes every five.
_heartbeat_cache = {"at": 0.0, "alive": False}


def bridge_folder():
    """The shared folder. Mirrors `BlenderBridge.folder()`.

    Both sides compute it rather than negotiate it: discovering it would need
    one of them to be running, and the point of the bridge is that Cariquima
    can be closed.
    """
    override = _prefs_value("folder")
    if override:
        return bpy.path.abspath(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", "") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "Cariquima", "bridge")


def _prefs_value(name):
    try:
        return getattr(bpy.context.preferences.addons[ADDON_ID].preferences, name)
    except (KeyError, AttributeError):
        return ""


def cariquima_is_running():
    """Is Cariquima open? Decided by the heartbeat, not the process list.

    Looking a process up by name needs an external library or a different
    command per system, and it would confuse two installations. The heartbeat
    is written by the application itself: if it is fresh, it is open.
    """
    now = time.time()
    if now - _heartbeat_cache["at"] < 2.0:
        return _heartbeat_cache["alive"]
    alive = False
    try:
        with open(os.path.join(bridge_folder(), HEARTBEAT), encoding="utf-8") as handle:
            alive = now - float(handle.read().strip()) < HEARTBEAT_STALE
    except (OSError, ValueError):
        alive = False
    _heartbeat_cache.update({"at": now, "alive": alive})
    return alive


def _split_selection(context):
    """Split the selection into low-poly and high-poly.

    The high-poly is recognised by its name suffix (`_HP`, `_high`) or by
    living in a collection named that way. It is the convention people who
    bake already use; inventing a new one would force a rename before sending.
    """
    marks = ("_hp", "_high", "_alta")
    low, high = [], []
    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        name = obj.name.lower()
        in_high_collection = any(
            collection.name.lower().endswith(marks + ("hp", "high"))
            for collection in obj.users_collection)
        if name.endswith(marks) or in_high_collection:
            high.append(obj)
        else:
            low.append(obj)
    return low, high


def _rough_faces(objects):
    """Faces WITHOUT evaluating modifiers.

    The panel redraws when the mouse moves. Evaluating the mesh there would
    freeze Blender on a sculpt with Subdivision, so the exact number is
    computed once, when sending.
    """
    return sum(len(obj.data.polygons) for obj in objects)


def _texture_sets(objects):
    """How many Texture Sets Cariquima will create.

    One per material. It is the count that surprises people: someone sends a
    character with six materials, six sets appear, and without knowing where
    they came from it looks like an importer bug.
    """
    slots = set()
    without = 0
    for obj in objects:
        used = [m.name for m in obj.data.materials if m is not None]
        if used:
            slots.update(used)
        else:
            without += 1
    return len(slots) + (1 if without else 0)


def audit(context):
    """Everything that can be known here and would cost a bake over there.

    Returns `[(level, text)]`. An ERROR blocks the send; the rest informs.
    It only reads cheap data (list lengths, object scale) because it runs on
    every panel redraw.
    """
    low, high = _split_selection(context)
    out = []

    if not low and not high:
        return [(INFO, "Select a mesh to send")]
    if not low:
        # Selecting ONLY the high-poly is an easy, silent mistake: without this
        # warning it would be exported as the low-poly and painted at 20
        # million faces.
        out.append((ERROR, "Only high-poly selected. The low-poly is what gets "
                           "painted — select it too"))

    # Without UVs there is nowhere to paint. It is the only real blocker:
    # everything else comes out odd, this does not come out at all.
    for obj in low:
        if not obj.data.uv_layers:
            out.append((ERROR, "“%s” has no UV map — nothing to paint on"
                        % obj.name))
        elif len(obj.data.uv_layers) > 1:
            active = next((uv.name for uv in obj.data.uv_layers if uv.active_render),
                          obj.data.uv_layers[0].name)
            out.append((INFO, "“%s” has %d UV maps · “%s” is the one that travels"
                        % (obj.name, len(obj.data.uv_layers), active)))
    for obj in low + high:
        if not obj.data.polygons:
            out.append((WARN, "“%s” has no faces" % obj.name))
        # Negative scale flips the normals, and a normal map baked against
        # flipped normals comes out sunken where it should stick out.
        if any(axis < 0.0 for axis in obj.scale):
            out.append((WARN, "“%s” has negative scale — normals will be flipped"
                        % obj.name))
        # Applying modifiers to a mesh with shape keys fails in the exporter,
        # and Blender's message does not say which object it was.
        if obj.data.shape_keys and obj.modifiers:
            out.append((WARN, "“%s” has shape keys and modifiers · export may "
                              "refuse to apply them" % obj.name))

    faces = _rough_faces(low)
    if faces > LOW_POLY_WARN_TRIS:
        out.append((WARN, "The low-poly has %s faces before modifiers · that is "
                          "a lot to paint on" % f"{faces:,}"))
    if low:
        sets = _texture_sets(low)
        out.append((INFO, "%d Texture Set%s · one per material"
                    % (sets, "" if sets == 1 else "s")))
    if not high:
        out.append((INFO, "No high-poly · Normal and Height cannot be baked. "
                          "Name it _HP and select it"))

    # The scene scale travels in the GLB: at 0.001 the object arrives a
    # thousand times smaller and the brush size has nothing to do with what
    # is on screen.
    scale = context.scene.unit_settings.scale_length
    if abs(scale - 1.0) > 1e-6:
        out.append((WARN, "Scene unit scale is %g, not 1 · the mesh will arrive "
                          "scaled" % scale))

    root = bridge_folder()
    parent = root if os.path.isdir(root) else os.path.dirname(root)
    if not os.access(parent if parent else ".", os.W_OK):
        out.append((ERROR, "Cannot write to the bridge folder: %s" % root))

    return out


def _blocking(report):
    return [item for item in report if item[0] == ERROR]


def _triangles(objects, depsgraph):
    """Count triangles with the modifiers ALREADY applied.

    Counting on the edit mesh would report 500 faces for an object that
    exports two million through a Subdivision Surface, and that number is
    exactly what Cariquima uses to decide whether the bake fits in memory.
    """
    total = 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        if mesh is None:
            continue
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        evaluated.to_mesh_clear()
    return total


def _export(objects, filepath, apply_modifiers):
    """Export those objects to GLB.

    GLB and not FBX. It is the fastest of the three formats Cariquima reads
    (Godot's FBX importer is by far the slowest), and here the file is an
    intermediate nobody opens by hand: there is no reason to pay for the
    expensive format.
    """
    previous = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    try:
        bpy.ops.export_scene.gltf(
            filepath=filepath,
            export_format="GLB",
            use_selection=True,
            export_apply=apply_modifiers,
            # Normals and UVs are the whole point: without UVs there is
            # nowhere to paint, and without normals the bake has nothing to
            # compare against.
            export_normals=True,
            export_texcoords=True,
            # Textures do not travel. Cariquima generates them; sending
            # Blender's would multiply the size of the send by ten only to
            # overwrite them.
            export_materials="NONE",
            export_animations=False,
            export_cameras=False,
            export_lights=False,
        )
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous:
            try:
                obj.select_set(True)
            except ReferenceError:
                pass
        bpy.context.view_layer.objects.active = active


def _launch_cariquima():
    """Open Cariquima. Returns an error message, or "" on success.

    It does not open if it is already running: a second instance over the
    same project folders is how work gets lost, with two saves overwriting
    each other.
    """
    if cariquima_is_running():
        return ""
    executable = bpy.path.abspath(_prefs_value("executable") or "")
    if not executable or not os.path.isfile(executable):
        return ("Set the Cariquima executable in the add-on preferences to open "
                "it from here")
    args = [executable]
    # Running from source, the executable is Godot and it needs to be told
    # which project. With a packaged .exe this stays empty.
    project = bpy.path.abspath(_prefs_value("project") or "")
    if project and os.path.isdir(project):
        args += ["--path", project]
    try:
        subprocess.Popen(args, cwd=os.path.dirname(executable))
    except OSError as error:
        return "Could not launch Cariquima: %s" % error
    return ""


class CariquimaPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    folder: bpy.props.StringProperty(
        name="Bridge folder",
        description=(
            "Leave empty for the default folder. Only change it if Cariquima "
            "runs under another user account or on a network drive"),
        subtype="DIR_PATH",
        default="",
    )
    executable: bpy.props.StringProperty(
        name="Cariquima executable",
        description="Lets the panel open Cariquima. Optional",
        subtype="FILE_PATH",
        default="",
    )
    project: bpy.props.StringProperty(
        name="Project folder",
        description=(
            "Only when the executable is Godot and Cariquima runs from source. "
            "With the packaged executable, leave empty"),
        subtype="DIR_PATH",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "folder")
        layout.label(text="In use: %s" % bridge_folder())
        layout.separator()
        layout.prop(self, "executable")
        layout.prop(self, "project")
        layout.operator("wm.url_open", text="Get Cariquima",
                        icon="URL").url = CARIQUIMA_URL


class CARIQUIMA_OT_send(bpy.types.Operator):
    bl_idname = "cariquima.send"
    bl_label = "Send to Cariquima"
    bl_description = "Export the selection and leave it ready for Cariquima"
    bl_options = {"REGISTER"}

    apply_modifiers: bpy.props.BoolProperty(
        name="Apply modifiers",
        description=(
            "Export what you see, not the cage. Without this a Subdivision "
            "Surface does not travel and you paint on a mesh that is not the "
            "one you render"),
        default=True,
    )
    launch: bpy.props.BoolProperty(
        name="Open Cariquima",
        description="Open it after sending, if it is not open already",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        # Edits made in Edit Mode live in the edit mesh, not in the mesh the
        # exporter reads: sending from Edit Mode used to ship the mesh as it
        # was BEFORE entering it, and the selection operators below refuse to
        # run there. Back to Object Mode for the send, and back again after.
        previous_mode = context.object.mode if context.object else "OBJECT"
        if previous_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            return self._send(context)
        finally:
            if previous_mode != "OBJECT" and context.object is not None:
                try:
                    bpy.ops.object.mode_set(mode=previous_mode)
                except RuntimeError:
                    pass

    def _send(self, context):
        # The audit runs again here instead of trusting the panel's: the
        # operator can be called from the console, a shortcut or another
        # script, and none of those draw the panel.
        blocking = _blocking(audit(context))
        if blocking:
            self.report({"ERROR"}, blocking[0][1])
            return {"CANCELLED"}

        low, high = _split_selection(context)
        root = bridge_folder()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        staging = os.path.join(root, "send_%s.tmp" % stamp)
        final = os.path.join(root, "send_%s" % stamp)
        if os.path.exists(staging):
            shutil.rmtree(staging)
        os.makedirs(staging, exist_ok=True)

        name = low[0].name
        depsgraph = context.evaluated_depsgraph_get()
        try:
            low_file = "%s_LP.glb" % name
            _export(low, os.path.join(staging, low_file), self.apply_modifiers)
            high_file = ""
            if high:
                high_file = "%s_HP.glb" % name
                _export(high, os.path.join(staging, high_file),
                        self.apply_modifiers)

            manifest = {
                "version": CONTRACT_VERSION,
                "name": name,
                "low": low_file,
                "high": high_file,
                "triangles_low": _triangles(low, depsgraph),
                "triangles_high": _triangles(high, depsgraph) if high else 0,
                "blender": bpy.app.version_string,
                "sent_at": time.time(),
            }
            with open(os.path.join(staging, "manifest.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=1)
        except Exception:
            # A half-written send is worse than none: Cariquima would offer it
            # and fail to open it, and the user would have no way of telling
            # which of the two programs got it wrong.
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # Only now does it exist for Cariquima. Renaming a folder is atomic.
        os.rename(staging, final)

        message = "Sent %s to Cariquima%s" % (
            name, " with high-poly" if high else "")
        if self.launch:
            failure = _launch_cariquima()
            if failure:
                # The send IS already in the folder and it is valid. Not being
                # able to open the application does not undo it, so it is
                # reported as a warning and not as a failed send.
                self.report({"WARNING"}, "%s · %s" % (message, failure))
                return {"FINISHED"}
        self.report({"INFO"}, message)
        return {"FINISHED"}


class CARIQUIMA_OT_open(bpy.types.Operator):
    bl_idname = "cariquima.open"
    bl_label = "Open Cariquima"
    bl_description = "Open Cariquima without sending anything"
    bl_options = {"REGISTER"}

    def execute(self, context):
        failure = _launch_cariquima()
        if failure:
            self.report({"ERROR"}, failure)
            return {"CANCELLED"}
        self.report({"INFO"}, "Cariquima is open")
        return {"FINISHED"}


class CARIQUIMA_PT_panel(bpy.types.Panel):
    bl_label = "Cariquima"
    bl_idname = "CARIQUIMA_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cariquima"

    def draw(self, context):
        layout = self.layout
        low, high = _split_selection(context)

        state = layout.row()
        running = cariquima_is_running()
        state.label(text="Cariquima: %s" % ("open" if running else "not open"),
                    icon="CHECKMARK" if running else "UNLINKED")
        if not running:
            state.operator(CARIQUIMA_OT_open.bl_idname, text="", icon="PLAY")
            # Someone who installed the add-on from GitHub may not have the
            # application yet. Without this the panel only says "not open"
            # and never where to get it.
            if not _prefs_value("executable"):
                layout.operator("wm.url_open", text="Get Cariquima",
                                icon="URL").url = CARIQUIMA_URL

        # Say BEFORE sending what goes as what. Finding out that the high-poly
        # travelled as the low-poly costs a whole bake.
        box = layout.box()
        box.label(text="Low-poly: %d object(s) · %s faces"
                       % (len(low), f"{_rough_faces(low):,}"))
        box.label(text="High-poly: %s" % (
            "%d object(s) · %s faces" % (len(high), f"{_rough_faces(high):,}")
            if high else "none"))

        report = audit(context)
        if report:
            checks = layout.box()
            for level, text in report:
                row = checks.row()
                row.alert = level == ERROR
                row.label(text=text, icon=_ICONS[level])

        column = layout.column()
        # Disabled, not hidden: a button that disappears reads as a broken
        # add-on, and the reason is already written above.
        column.enabled = not _blocking(report)
        column.operator(CARIQUIMA_OT_send.bl_idname, icon="EXPORT")
        send_open = column.operator(CARIQUIMA_OT_send.bl_idname,
                                    text="Send and Open Cariquima", icon="PLAY")
        send_open.launch = True


CLASSES = (CariquimaPreferences, CARIQUIMA_OT_send, CARIQUIMA_OT_open,
           CARIQUIMA_PT_panel)


def register():
    for item in CLASSES:
        bpy.utils.register_class(item)


def unregister():
    for item in reversed(CLASSES):
        bpy.utils.unregister_class(item)


if __name__ == "__main__":
    register()
