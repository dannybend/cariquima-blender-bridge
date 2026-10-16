# Send to Cariquima: Blender add-on

Send your low-poly and high-poly from Blender to
**[Cariquima Texture Painter](https://dannybend.itch.io/cariquima-3d-texturing-software)**
in one click, ready to bake and paint.

Without the add-on, getting a mesh into a texture painter takes six steps:
export, pick a folder, pick a format, remember which one is the high-poly,
switch programs, and import. With it, you select and press **Send**.

## What it does

- **Splits the selection into low-poly and high-poly** using the naming
  convention bakers already use: an object or collection ending in `_HP`,
  `_high` or `_alta` travels as the high-poly, and everything else as the
  low-poly.
- **Checks before sending** everything that would otherwise cost you a bake to
  discover:
  - Missing UVs block the send.
  - It warns about negative scale (flipped normals), a scene unit scale that
    is not 1, shape keys combined with modifiers, and a "low-poly" that is
    not low.
  - It tells you how many Texture Sets Cariquima will create (one per
    material) and which UV map travels when there are several.
- **Exports what you see.** Modifiers are applied, so a Subdivision Surface
  arrives subdivided. Edits made in Edit Mode are included.
- **Opens Cariquima** if you want, and never starts a second copy when it is
  already running.

Cariquima does not need to be open. Sends wait in a shared folder for up to a
week, and Cariquima offers to open them as soon as it is running.

## Install

Requires **Blender 4.2 or newer** (tested on 5.1).

### From the extension repository (recommended: updates arrive inside Blender)

1. *Edit > Preferences > Get Extensions*, open the **Repositories** menu (⌄,
   top right) and press **+ > Add Remote Repository**.
2. Paste this URL and press OK:

   ```
   https://dannybend.github.io/cariquima-blender-bridge/index.json
   ```

3. Search for **Send to Cariquima** in *Get Extensions* and press **Install**.
   From then on, new versions show up in Blender's own update list.

### From a ZIP

1. Download `cariquima_bridge-x.y.z.zip` from
   [Releases](https://github.com/dannybend/cariquima-blender-bridge/releases).
2. Drag the ZIP onto the Blender window, or go to *Edit > Preferences > Get
   Extensions > ⌄ > Install from Disk…*

Either way, the panel is in the 3D View sidebar (**N**), **Cariquima** tab.

Optional: in the add-on preferences, set the path to `Cariquima.exe` so the
panel can open it for you.

## Use

1. Select the low-poly and, if you have one, the high-poly (`MyProp` and
   `MyProp_HP`).
2. Read the checks in the panel. Red ones block the send; the others are
   warnings.
3. Press **Send to Cariquima** or **Send and Open Cariquima**.

## How it works

The add-on writes each send to a folder that both programs compute on their
own. Nothing listens on a network port, so there are no firewall prompts:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\Cariquima\bridge` |
| macOS | `~/Library/Application Support/Cariquima/bridge` |
| Linux | `$XDG_DATA_HOME/Cariquima/bridge` or `~/.local/share/Cariquima/bridge` |

Each send is a folder with `<name>_LP.glb`, an optional `<name>_HP.glb`, and a
`manifest.json` that holds the contract version, the triangle counts and the
Blender version. The folder is written as `send_<time>.tmp` and renamed when it
is complete, so Cariquima never sees a half-written file.

Cariquima writes a `running.lock` heartbeat every five seconds while it is
open. The panel reads it to show whether Cariquima is open.

The add-on only reads your selection and writes to that folder. It does not
connect to the internet. The *Get Cariquima* button only opens the store page
in your browser.

## About Cariquima

Cariquima is a PBR texture painter for game artists, currently in open beta on
Windows, with a [free demo](https://dannybend.itch.io/cariquima-3d-texturing-software).
You can paint nine PBR channels straight on the model, bake mesh maps from the
high-poly, build materials from layers, masks and Smart Materials that react to
the geometry, and export for Unreal or Unity.

Cariquima itself is not open source. This add-on is.

## License

[GPL-3.0-or-later](LICENSE), like every Blender add-on.

Bug reports and ideas are welcome in
[Issues](https://github.com/dannybend/cariquima-blender-bridge/issues).
