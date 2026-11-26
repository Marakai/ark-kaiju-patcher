# ark-kaiju-patcher

Server-side fixes for a set of Steam Workshop kaiju mods in ARK: Survival Evolved, on a
**dedicated server you control**:

| Script | What it does |
|---|---|
| `ark_kaiju_patch.py` | Kaiju visible from far away instead of popping in next to you |
| `ark_kaiju_teams.py` | Kaiju fight each other (three hostile sides) instead of a near-total truce |
| `ark_marnii_cats.py` | Bonus, unrelated mod: makes MarniiMods Wildlife cats spawn on maps other than Lost Island |

All three edit files **already on your server's disk**, ship no mod assets, are idempotent, validate
every file after patching, write atomically, back up the first original, offer `--dry-run` and
`--restore`, and refuse to run while the server is running. Python 3, standard library only. Keep the
scripts in one folder (the other two import `ark_kaiju_patch.py`).

## Quick start
```
# stop the ARK server first
python3 ark_kaiju_patch.py --dry-run && python3 ark_kaiju_patch.py
python3 ark_kaiju_teams.py --dry-run && python3 ark_kaiju_teams.py
python3 ark_marnii_cats.py --dry-run && python3 ark_marnii_cats.py     # only if you run MarniiMods Wildlife
# start the server, then (as admin, optional) respawn wild creatures so they pick up the changes:
#   admincheat destroywilddinos
```
`--dry-run` exits 2 if something would change. Use `--mods-dir` if your server is not at
`/home/arkserver/ARK/ShooterGame/Content/Mods`, and `--backup-dir` to choose where originals go
(defaults are under `~/ARKModtests/`). See each script's `--help`.

# 1. Visibility: `ark_kaiju_patch.py`

## The problem
Several kaiju mods ship creature blueprints with missing or small network relevance settings.
The server only sends a creature to a client while it is "relevant", so a building-sized monster
appears from nowhere a few hundred metres away. The key missing flag is
`bUseNetworkSpatialization=False`; the script also raises `NetCullDistanceSquared` (default 5 km).

## What the script does
`ark_kaiju_patch.py` edits the `*_Character_BP.uasset` files of these mods:

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

## Usage
```
python3 ark_kaiju_patch.py --dry-run     # show what would change
python3 ark_kaiju_patch.py               # apply (server STOPPED)
python3 ark_kaiju_patch.py --restore     # put originals back
python3 ark_kaiju_patch.py --netcull-m 3000 --exact   # lower range if performance suffers
```
Use `--mods-dir` if your server is not at `/home/arkserver/ARK/ShooterGame/Content/Mods`
(see `--help`). Start the server again afterwards.

## Performance
Raising the relevance range means the server replicates every kaiju within 5 km to every client in
range, and clients keep those creatures loaded, animated and drawn at distances where vanilla ARK
would have culled them. With several kaiju active, expect lower client frame rates, more server CPU
and network traffic, and long-range sounds. Large spawn counts multiply this. If it hurts, lower the
range (`--netcull-m 3000 --exact`) or reduce spawn rates. Dropping the range is safe at any time,
since the script is idempotent and `--restore` puts the originals back.

The upside is the whole point: you see giant kaiju as they should be seen, as looming threats from
far away, instead of monsters that appear out of nowhere next to you. For many players that changes
the feel of the game, and it is worth the cost.

## Caveats - read these
- Tested on **one** server (preaquatica branch, mods frozen) with the mod versions of Nov 2026.
  Other versions may differ; the script skips files it can't validate.
- **Back up your mods first.** Use at your own risk.
- A Workshop update or `validate` on the server overwrites the patch; just re-run the script.
- Server-side only. Draw distance is client-side, so use `--export-dir` if you want to copy
  patched files to clients (clients' Steam verify will undo that).
- Not affiliated with the mod authors or Wildcard. Mods belong to their authors.

## Colours
Event/mutation colours are not visible on many of these kaiju because their materials have no
colourisation shader permutation. That is a client-side, hand-edit-the-assets job and is **not**
done by the script. If you are adventurous, see [COLOUR-HOWTO.md](COLOUR-HOWTO.md) for what is
wrong and how it was fixed on one setup. It is experimental and ships no assets.

This is purely for fun, especially during events like Easter, when every spawn gets crazy colours.
Turquoise and sky-blue Evolved Godzilla? Pink Ghidorah? Silly, but fun.

# 2. Who fights whom: `ark_kaiju_teams.py`

## The problem
Out of the box most of these kaiju share one team, so they leave each other alone (a near-total
truce), while Evolved Godzilla attacks everybody. That is no fun if you want Godzilla to fight
Ghidorah.

## How ARK decides
Each wild creature has a `TargetingTeamNameOverride`. That name must be one of the teams defined in
the base game's `NamedTeamDefinitions` (in `COREMEDIA_PrimalGameData_BP`); each definition says which
other teams it attacks. **A made-up name does not create a new team**: a creature with an undefined
team name never *starts* a fight with other wild creatures, it only fights back. Only three
definitions attack every team except their own:

| Team | Attacks everyone except | Used by (vanilla) |
|---|---|---|
| `Carnivores_High` | own team, water carnivores | Allosaurus and hundreds of other creature blueprints |
| `Robot_Aggressive` | own team, `Robot_Neutral` | Defender (Extinction) |
| `Corrupt` | own team | corrupted creatures (Extinction) |

So without editing the base game you get at most three mutually hostile sides.

## What the script sets
| Kaiju | Team | Result |
|---|---|---|
| Super Godzilla | `Corrupt` | the lone end boss, attacks every other kaiju |
| Evolved Godzilla, Shin Godzilla | `Robot_Aggressive` | allies, attack everyone else |
| Ghidorah, Kiryu, Gigan, Muto | `Carnivores_High` (unchanged) | allies, attack everyone else |
| Rodan | `Carnivores_Medium_TargetPlayerOrTamed` | goes for players and tames only, but other kaiju hunt it |

Rodan's two "ignore wild dinos" flags are set to False. With them True, nothing ever fought Rodan and
it overspawned. Players and tames stay targets for every kaiju. Want a different line-up? Edit the
`RULES` table at the top of the script, but only use team names that exist in the base game.

## Notes
- Expect a lot of fighting and destruction: kaiju AOE hits bases near a fight. Back up your world.
- Changes apply to newly spawned creatures; `admincheat destroywilddinos` respawns everything.
- On Extinction, `Corrupt` Super Godzilla would side with the corrupted creatures there.
- `--restore` puts back the original mod files (the mods' original teams).

# 3. Bonus: MarniiMods Wildlife cats, `ark_marnii_cats.py`

The no-longer-maintained ASE mod **MarniiMods Wildlife** (1984936918) gives its domestic cat exactly
one spawn entry, in Lost Island's own spawn container. Cats are plentiful there and (nearly) absent
on The Island, Ragnarok, Fjordur and others, while the mod's other animals spawn fine.

The script clones the cat's entry into more of the shared vanilla containers in
`PrimalGameData_BP_MarniiModsWildlife.uasset`. Default: `Grassland,Jungle`. `Mountain` also works but
puts cats on cliff faces; `Beach`, `Snow`, `Swamp` exist too. Existing entries are never modified.
```
python3 ark_marnii_cats.py --containers Grassland,Jungle   # default
python3 ark_marnii_cats.py --weight 0.3                     # more cats (original weight 0.1)
python3 ark_marnii_cats.py --restore                        # original file back
```
To remove a container again: `--restore`, then run with the list you want.

## Licence
MIT (see LICENSE).
