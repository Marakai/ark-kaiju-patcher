#!/usr/bin/env python3
"""
ark_marnii_cats.py - idempotent spawn fix for the ARK: Survival Evolved mod "MarniiMods Wildlife" (1984936918).

Problem: the mod's game-data blueprint adds creatures to the map spawn containers through
TheNPCSpawnEntriesContainerAdditions. The Cat has exactly ONE entry, in DinoSpawnEntriesRedwoods_LostIsland,
the container of the Lost Island map only - so cats are plentiful there and (nearly) absent elsewhere.
Cattle, wolves, camels etc. have entries in the shared vanilla containers and spawn on many maps.

Fix: clone the Cat's entry (a self-contained block) into further containers. Only the container class
reference of each clone differs. Existing entries are never modified.

Usage:
  ark_marnii_cats.py              apply (refuses if the ARK server is running)
  ark_marnii_cats.py --dry-run    show what would change; exit 2 if anything is pending
  ark_marnii_cats.py --restore    put the backed-up file back
  --containers A,B,C              containers to add cats to (default: Grassland,Jungle)
  --weight 0.1                    EntryWeight of the NEW cat entries (default: same as the original, 0.1)
Server must be STOPPED. Python 3, standard library only; needs ark_kaiju_patch.py in the same folder.
"""
import argparse
import os
import re
import shutil
import struct
import sys

from ark_kaiju_patch import UAsset, PatchError, DEFAULT_MODS_DIR, atomic_write, server_running

MOD = '1984936918'
FNAME = 'PrimalGameData_BP_MarniiModsWildlife.uasset'
ARRAY = 'TheNPCSpawnEntriesContainerAdditions'
DEFAULT_BACKUP_DIR = os.path.expanduser('~/ARKModtests/auto-backups-marnii')
DEFAULT_CONTAINERS = 'Grassland,Jungle'


def is_cat(e):
    return re.match(r'cat\b', e['name'], re.I) is not None   # 'Cat (1)', not 'Cattle (1-4)'


def locate(data):
    a = UAsset(data)
    de = a.default_export()
    ps, _ = a.props(de)
    p = [x for x in ps if x[0] == ARRAY]
    if not p or p[0][1] != 'ArrayProperty':
        raise PatchError(f'{ARRAY} not found')
    vo, size = p[0][4], p[0][3]
    count = struct.unpack_from('<i', a.d, vo)[0]
    sce = struct.pack('<ii', a.names.index('SpawnEntriesContainerClass'), 0)
    aen = struct.pack('<ii', a.names.index('AnEntryName'), 0)
    starts = [m.start() for m in re.finditer(re.escape(sce), a.d[vo + 4:vo + size])]
    starts = [vo + 4 + s for s in starts]
    if len(starts) != count or starts[0] != vo + 4:
        raise PatchError(f'element scan ({len(starts)}) does not match array count ({count})')
    ends = starts[1:] + [vo + size]
    elems = []
    for s, e in zip(starts, ends):
        ref = struct.unpack_from('<i', a.d, s + 24)[0]
        cont = a.imports[-ref - 1][1] if ref < 0 else None
        m = a.d.find(aen, s, e)
        ename = ''
        if m >= 0:
            n = struct.unpack_from('<i', a.d, m + 24)[0]
            ename = a.d[m + 28:m + 28 + n - 1].decode('latin1') if n > 0 else ''
        elems.append({'start': s, 'end': e, 'ref': ref, 'cont': cont, 'name': ename})
    return a, de, vo, size, count, elems


def build(data, wanted, weight):
    a, de, vo, size, count, elems = locate(data)
    d = bytearray(a.d)
    L = len(d)
    cats = [e for e in elems if is_cat(e)]
    if not cats:
        raise PatchError('no original Cat entry found to clone')
    tmpl = cats[0]
    block = bytearray(d[tmpl['start']:tmpl['end']])
    refs = {}
    for e in elems:
        if e['cont']: refs.setdefault(e['cont'], e['ref'])
    have = {e['cont'] for e in cats}
    log, new_blocks = [], b''
    for w in wanted:
        cname = f'DinoSpawnEntries{w}_C'
        if cname in have:
            continue
        if cname not in refs:
            raise PatchError(f'container {cname} is not referenced by this mod (no import to reuse)')
        b = bytearray(block)
        struct.pack_into('<i', b, 24, refs[cname])
        if weight is not None:
            ew = struct.pack('<ii', a.names.index('EntryWeight'), 0)
            m = b.find(ew)
            if m < 0: raise PatchError('EntryWeight not found in Cat entry')
            struct.pack_into('<f', b, m + 24 + 8, weight)   # tag 24 bytes, +8 inner type name
        new_blocks += bytes(b)
        log.append(f'Cat entry added to {cname}')
    if not log:
        return bytes(a.d), []
    add = len(new_blocks)
    n_new = len(log)
    # sanity: layout expectations
    if a.exports[0]['off'] != a.hdr_size or max(e['off'] + e['size'] for e in a.exports) != L - 4:
        raise PatchError('export data layout differs from expectation')
    if struct.unpack_from('<I', d, 155)[0] != L - 4:
        raise PatchError('header layout differs from expectation at byte 155')
    struct.pack_into('<i', d, vo, count + n_new)            # array count
    struct.pack_into('<i', d, vo - 16, size + add)           # array property size (tag: ... size, idx, inner name)
    struct.pack_into('<I', d, 155, L - 4 + add)
    pos = vo + size
    for e in a.exports:
        shift = add if e['off'] > de['off'] else 0
        sz = e['size'] + (add if e is de else 0)
        struct.pack_into('<ii', d, e['entry'] + 24, sz, e['off'] + shift)
    d[pos:pos] = new_blocks
    return bytes(d), log


def validate(orig, new, wanted, weight):
    a, b = UAsset(orig), UAsset(new)
    if b.names != a.names or b.imports != a.imports:
        raise PatchError('validation: name/import tables changed')
    if len(a.exports) != len(b.exports):
        raise PatchError('validation: export count changed')
    if b.exports[0]['off'] != b.hdr_size or max(e['off'] + e['size'] for e in b.exports) != len(b.d) - 4:
        raise PatchError('validation: export layout inconsistent')
    if b.d[-4:] != a.d[-4:]:
        raise PatchError('validation: file tail changed')
    _, _, voa, sa, ca, ea = locate(orig)
    _, _, vob, sb, cb, eb = locate(new)
    # original elements must be byte-identical and keep their order at the front
    for x, y in zip(ea, eb):
        if orig[x['start']:x['end']] != new[y['start']:y['end']]:
            raise PatchError('validation: an original element changed')
    if [e['cont'] for e in eb[:len(ea)]] != [e['cont'] for e in ea]:
        raise PatchError('validation: element order changed')
    # export data outside the array must be unchanged (the header's export table legitimately changes)
    if b.hdr_size != a.hdr_size or orig[:24] != new[:24] or orig[28:155] != new[28:155]:
        raise PatchError('validation: header changed unexpectedly')
    h = a.hdr_size
    if orig[h:voa - 16] != new[h:voa - 16]:
        raise PatchError('validation: bytes before the array changed')
    ea_end = voa + sa
    eb_end = vob + sb
    if orig[ea_end:] != new[eb_end:]:
        # exports table lives in the header, so only data after the array is compared here
        raise PatchError('validation: bytes after the array changed')
    have = {e['cont'] for e in eb if is_cat(e)}
    for w in wanted:
        if f'DinoSpawnEntries{w}_C' not in have:
            raise PatchError(f'validation: no cat entry for {w}')


def main():
    ap = argparse.ArgumentParser(description='Add Cat spawn entries to more containers (MarniiMods Wildlife).')
    ap.add_argument('--mods-dir', default=DEFAULT_MODS_DIR)
    ap.add_argument('--backup-dir', default=DEFAULT_BACKUP_DIR)
    ap.add_argument('--containers', default=DEFAULT_CONTAINERS)
    ap.add_argument('--weight', type=float, default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--force', action='store_true', help='skip the server-running check')
    ap.add_argument('--file', help='operate on this file directly (testing)')
    args = ap.parse_args()
    wanted = [c.strip() for c in args.containers.split(',') if c.strip()]
    path = args.file
    if not path:
        mods_dir = os.path.abspath(args.mods_dir)
        path = None
        for r, _, fs in os.walk(os.path.join(mods_dir, MOD)):
            if FNAME in fs:
                path = os.path.join(r, FNAME); break
        if not path:
            sys.exit(f'{FNAME} not found under {mods_dir}/{MOD}')
    if server_running() and not args.dry_run and not args.force and not args.file:
        sys.exit('ARK SERVER IS RUNNING - not touching any files.\n'
                 'Shut the server down first (this script will not stop it), run again, then start it.')
    bk = os.path.join(args.backup_dir, FNAME)
    try:
        if args.restore:
            if not os.path.exists(bk): sys.exit('no backup found')
            if not args.dry_run: atomic_write(path, open(bk, 'rb').read())
            print(f'[restore] {path}'); return
        orig = open(path, 'rb').read()
        new, log = build(orig, wanted, args.weight)
        if not log:
            print('[ok   ] all requested containers already have a Cat entry'); return
        validate(orig, new, wanted, args.weight)
        for l in log: print('   ', l)
        if args.dry_run:
            print('[would] patch', path); sys.exit(2)
        if not os.path.exists(bk):
            os.makedirs(os.path.dirname(bk), exist_ok=True); shutil.copy2(path, bk)
        atomic_write(path, new)
        validate(orig, open(path, 'rb').read(), wanted, args.weight)
        print('[patch]', path)
        print('>>> Start the server again for this to take effect (this script never starts/stops it).')
    except (PatchError, OSError) as e:
        sys.exit(f'[ERROR] {e}')


if __name__ == '__main__':
    main()
