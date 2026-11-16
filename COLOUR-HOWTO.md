# COLOUR-HOWTO: making kaiju show their event/mutation colours

**Status: experimental, for the adventurous.** This is a description of a method that worked on one
private server and one Windows client, for nine kaiju (Kiryu, Ghidorah, Super Godzilla, Evolved
Godzilla, Rodan, Muto/Femuto, Shin). No tooling or modified assets are included here, because the
assets are other people's mods. This document tells you what is wrong and how it was fixed, so
you can redo it for the mods you care about.

## Symptom
You spawn a kaiju with Easter-event mutations (or use `cheat SetTargetDinoColor <region> <colour>`).
A spyglass-type tool shows the colour regions are set, but the creature on screen keeps its stock
colours. Nothing is wrong with your server.

## Cause
ASE colours a creature through a mask texture and a master material function. Three things must hold:

1. The skin material (a Material Instance Constant, "MIC") needs a **colour-region mask** texture
   (hues red, green, blue, yellow, cyan, magenta = regions 0-5).
2. The MIC's compiled shader must be the **`IsColorized=True`** permutation. The master material's
   default is False, and a cooked MIC carries its own compiled shader maps, so flipping a
   parameter isn't enough. Without the SDK you cannot compile one.
3. The creature blueprint needs `bUseColorization=True`, or live recolouring doesn't refresh.

These kaiju mods fail on 1 and 2 (and sometimes 3).

## The method in one paragraph
Take a MIC from a mod that already has a working colourised material (this was done with the
NightFury mod 3538861264, whose parent is `MM_GEN_Dino_NoMetallic_Colorized_MIC`). Clone it, re-point
its texture references to a grey base texture and a generated mask for your creature, and install it
under the **original material's asset path** so the mesh picks it up. Set `bUseColorization=True`
on the blueprint.

## When you would (and wouldn't) do this
- Do it if you play on a private server, keep the mods frozen, and accept hand-editing binary assets.
- Don't do it on a server with public clients: **colour is rendered client-side, so every player
  needs the modified files**, and Workshop re-downloads or Steam "verify" silently undo them.
- Don't do it if you can't afford an occasional client crash while you experiment.

## Where the changes go
| Change | Server | Client |
|---|---|---|
| `bUseColorization=True` on `*_Character_BP.uasset` | yes | yes |
| New grey-base and mask textures | no | yes (Content/Mods/<modid>/) |
| Replaced skin materials | no | yes |

Keep unverified materials off the server: a broken material crashes whoever loads it, and the server
doesn't render colour anyway. Windows client path: `ShooterGame\Content\Mods\<modid>\...` inside the ARK
install folder.

## Steps
Always work on copies, with the game closed, and keep the originals.

1. **Pick the creature and list its skin materials.** Parse the blueprint and the mesh's material
   slots (see "File format notes"). Note the albedo and normal textures each material uses.
2. **Make a grey base texture** from the albedo: desaturate it so the colour regions can tint it.
   Keep the texture format (DXT1, or DXT5 with constant alpha 255) and its dimensions.
3. **Make a mask texture** the same size. Paint (or auto-generate by clustering the albedo) region
   colours: pure red = region 0, green = 1, blue = 2, yellow = 3, cyan = 4, magenta = 5. Region count is your
   choice, but 2-3 regions per skin looks best. The auto-generated masks used here are crude.
4. **Write the textures as new assets** by cloning an existing texture package of the same format and
   replacing the pixel data. New short names at the mod root work (the tested packs used names like
   `GhBodyG`, `GhBodyM`). Inline mip offsets and header fields must be rewritten to match.
5. **Clone the template MIC.** Re-point its texture parameters: Albedo Map = your grey base,
   ColorizationMask = your mask, Normal Map = the creature's own normal (or
   `/Engine/EngineMaterials/FlatNormal`), Material Map = `/Game/PrimalEarth/General/GreyTexture`.
6. **Keep the file length exactly identical.** The compiled shader data stores absolute file
   offsets. If the name table changes size, pad it with a dummy name so the total length stays the
   same. A shorter or longer file crashes the client at mod load with
   `Bad name index ... / ...` in `FMaterialShaderMap::Serialize`.
7. **Install** the cloned MIC under the original material's asset path in the client's mod folder,
   plus the new textures beside it.
8. **Set the flag** `bUseColorization=True` in the creature's blueprint on both client and server
   (the `ark_kaiju_patch.py` script deliberately doesn't touch this).
9. **Test** one creature first: spawn it, run `cheat SetTargetDinoColor 0 5` and similar, and
   check it changes live. Then do the rest.

## File format notes (ASE cooked .uasset, UE4 file version 404)
- Single-file packages, no name hashes, no property GUID byte, 32-bit export size/offset.
- Header fields at fixed byte offsets: header size at 24; name count at 41 and 105; export offset
  at 53; import count at 57; import offset at 61; depends offset at 65; header-size-minus-4 at 73
  and 151; file end or bulk-data start at 155.
- Tagged properties: name (8), type (8), size (4), array index (4), plus 8 more bytes for
  Struct/Byte/Array, then the value. A bool is one byte after the tag with size 0. The list ends
  with a `None` name.
- Resizing anything inside a cooked file means fixing every offset after it. Changes that keep
  the length (as in step 6) avoid this.

## Known limitations
- Emissive glow is lost on recoloured materials (the master has no emissive slot).
- Metal/roughness becomes flat stock grey.
- Mask quality depends on how well the regions were drawn.
- Tested on one client/server (preaquatica branch, Nov 2026). Mods differ; your results may too.

## Risks, plainly
- A bad file can crash the client at load. Test with the game restarted after each creature.
- Steam verify and Workshop re-downloads revert everything; keep your modified files in a safe folder.
- Redistributing modified mod files is likely a licence issue. Share methods, not assets.
