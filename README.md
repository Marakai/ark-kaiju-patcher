# ark-kaiju-patcher

Fixes the "kaiju pop in right next to me" problem in ARK: Survival Evolved for a set of
Steam Workshop kaiju mods, on a **dedicated server you control**.

## The problem
Several kaiju mods ship creature blueprints with missing or small network relevance settings.
The server only sends a creature to a client while it is "relevant", so a building-sized monster
appears from nowhere a few hundred metres away. The key missing flag is
`bUseNetworkSpatialization=False`; the script also raises `NetCullDistanceSquared` (default 5 km).

## What the script does
`ark_kaiju_patch.py` edits the `*_Character_BP.uasset` files of these mods **already on your server's disk**:

| Mod ID | Mod |
|---|---|
| 2474998329 | Kaiju (Muto) |
| 2489532823 | Mothra |
| 2514065085 | Godzilla pack (Godzilla, Ghidorah, Rodan, Evolved, Mecha) |
| 2635615490 | Super Godzilla |
| 2667389170 | Pacific Rim Kaiju (Raijin) |
| 2832142815 | Shin Godzilla |
| 2917197925 | Gigan |
| 2921712374 | Hedorah |
| 2923407686 | Kiryu |

It ships no mod assets. It is idempotent, validates every file after patching, writes atomically,
backs up the first original, and refuses to run while the server is running.

## Usage
```
python3 ark_kaiju_patch.py --dry-run     # show what would change
python3 ark_kaiju_patch.py               # apply (server STOPPED)
python3 ark_kaiju_patch.py --restore     # put originals back
python3 ark_kaiju_patch.py --netcull-m 3000 --exact   # lower range if performance suffers
```
Use `--mods-dir` if your server is not at `/home/arkserver/ARK/ShooterGame/Content/Mods`
(see `--help`). Start the server again afterwards.

## Caveats - read these
- Tested on **one** server (preaquatica branch, mods frozen) with the mod versions of Nov 2026.
  Other versions may differ; the script skips files it can't validate.
- **Back up your mods first.** Use at your own risk.
- A Workshop update or `validate` on the server overwrites the patch; just re-run the script.
- Server-side only. Draw distance is client-side, so use `--export-dir` if you want to copy
  patched files to clients (clients' Steam verify will undo that).
- Expect more render and network load when many kaiju are in range, and distant sounds.
- Not affiliated with the mod authors or Wildcard. Mods belong to their authors.

## Colours
Event/mutation colours are not visible on many of these kaiju because their materials have no
colourisation shader permutation. That is a client-side, hand-edit-the-assets job and is **not**
done by the script. If you are adventurous, see [COLOUR-HOWTO.md](COLOUR-HOWTO.md) for what is
wrong and how it was fixed on one setup. It is experimental and ships no assets.

## Licence
MIT (see LICENSE).
