#!/usr/bin/env python3
"""
ark_kaiju_teams.py - idempotent "who fights whom" patch for the ARK: Survival Evolved kaiju mods.

Background: wild creatures use TargetingTeamNameOverride, which must name a team defined in the base game's
NamedTeamDefinitions (COREMEDIA_PrimalGameData_BP). Undefined names never START fights with wild creatures (they only
retaliate). Teams that attack every team except their own: Corrupt, Robot_Aggressive, Carnivores_High.
Carnivores_Medium_TargetPlayerOrTamed attacks only players/tames but can itself be targeted.

Current design (three mutually hostile sides; all kaiju stay hostile to players):
  Super Godzilla   Corrupt            (alone)
  Evolved, Shin    Robot_Aggressive   (allies)
  Ghidorah, Kiryu, Gigan, Femuto: untouched (Carnivores_High)
  Rodan            Carnivores_Medium_TargetPlayerOrTamed, ignore-wild flags False (hunts players/tames, is hunted)

This is server-side only (AI targeting runs on the server). Run with the server STOPPED.
Safe to repeat; files already in the desired state are left alone. First originals are backed up.

Usage:
  ark_kaiju_teams.py              apply (refuses if the ARK server is running)
  ark_kaiju_teams.py --dry-run    show what would change; exit 2 if anything is pending
  ark_kaiju_teams.py --restore    put the backed-up files back
Python 3, standard library only; needs ark_kaiju_patch.py in the same folder.
"""
import argparse
import os
import shutil
import struct
import sys

from ark_kaiju_patch import (UAsset, PatchError, DEFAULT_MODS_DIR, atomic_write, server_running)

DEFAULT_BACKUP_DIR = os.path.expanduser('~/ARKModtests/auto-backups-teams')

# blueprint file name -> (mod id, {'team': name} and/or {'bools': {prop: value}})
RULES = {
    'SuperGodzilla_Character_BP.uasset':   ('2635615490', {'team': 'Corrupt'}),
    'EvolvedGodzilla_Character_BP.uasset': ('2514065085', {'team': 'Robot_Aggressive'}),
    'ShinGodzilla_Character_BP.uasset':    ('2832142815', {'team': 'Robot_Aggressive'}),
    'Rodan_Character_BP.uasset':           ('2514065085', {'team': 'Carnivores_Medium_TargetPlayerOrTamed',
                                                           'bools': {'bTargetingIgnoredByWildDinos': False,
                                                                     'bTargetingIgnoreWildDinos': False}}),
}
TEAM_PROP = 'TargetingTeamNameOverride'
ALLOWED = {TEAM_PROP, 'bTargetingIgnoredByWildDinos', 'bTargetingIgnoreWildDinos'}


def build(data, rule):
    a = UAsset(data)
    d = bytearray(a.d)
    L = len(d)
    log, new_names, insertions = [], [], []

    def name_index(s):
        if s in a.names: return a.names.index(s)
        if s not in new_names: new_names.append(s)
        return a.name_count + new_names.index(s)

    de = a.default_export()
    props, term = a.props(de)
    cur = {p[0]: p for p in props}

    if 'team' in rule:
        p = cur.get(TEAM_PROP)
        if not p or p[1] != 'NameProperty' or p[3] != 8:
            raise PatchError(f'{TEAM_PROP} missing or not a name property')
        if p[2] != rule['team']:
            struct.pack_into('<ii', d, p[4], name_index(rule['team']), 0)
            log.append(f'{TEAM_PROP}: {p[2]} -> {rule["team"]}')
    for pn, want in rule.get('bools', {}).items():
        p = cur.get(pn)
        if p:
            if p[1] != 'BoolProperty':
                raise PatchError(f'{pn} is not a bool property')
            if p[2] != want:
                d[p[4]] = 1 if want else 0
                log.append(f'{pn}: {p[2]} -> {want}')
        else:
            insertions.append((term, struct.pack('<iiiiiiB', name_index(pn), 0, name_index('BoolProperty'),
                                                 0, 0, 0, 1 if want else 0), de))
            log.append(f'{pn}: (unset) -> {want} [inserted]')

    if not log:
        return bytes(a.d), []
    if insertions or new_names:
        nb = b''
        for s in new_names:
            e = s.encode('latin1') + b'\0'
            nb += struct.pack('<i', len(e)) + e
        S, k = len(nb), len(new_names)
        total_ins = sum(len(b) for _, b, _ in insertions)
        u32 = lambda o: struct.unpack_from('<I', d, o)[0]
        w32 = lambda o, v: struct.pack_into('<I', d, o, v)
        expect = {41: a.name_count, 105: a.name_count, 24: a.hdr_size, 53: a.exp_off, 61: a.imp_off,
                  65: a.dep_off, 73: a.hdr_size - 4, 151: a.hdr_size - 4, 155: L - 4}
        for pos, val in expect.items():
            if u32(pos) != val:
                raise PatchError(f'header layout differs from expectation at byte {pos}')
        if a.name_end != a.imp_off:
            raise PatchError('name table is not directly followed by the import table')
        if a.exports[0]['off'] != a.hdr_size or max(e['off'] + e['size'] for e in a.exports) != L - 4:
            raise PatchError('export data layout differs from expectation')
        w32(41, a.name_count + k); w32(105, a.name_count + k)
        w32(24, a.hdr_size + S)
        w32(53, a.exp_off + S); w32(61, a.imp_off + S); w32(65, a.dep_off + S)
        w32(73, a.hdr_size - 4 + S); w32(151, a.hdr_size - 4 + S)
        w32(155, L - 4 + S + total_ins)
        for e in a.exports:
            add = sum(len(b) for _, b, ex in insertions if ex is e)
            shift = S + sum(len(b) for pos, b, _ in insertions if pos < e['off'])
            struct.pack_into('<ii', d, e['entry'] + 24, e['size'] + add, e['off'] + shift)
        for pos, b, _ in sorted(insertions, key=lambda t: -t[0]):
            d[pos:pos] = b
        d[a.imp_off:a.imp_off] = nb
    return bytes(d), log


def check_state(data, rule):
    a = UAsset(data)
    p = {x[0]: x for x in a.props(a.default_export())[0]}
    if 'team' in rule and (TEAM_PROP not in p or p[TEAM_PROP][2] != rule['team']):
        return False
    return all(pn in p and p[pn][2] == want for pn, want in rule.get('bools', {}).items())


def validate(orig, new, rule):
    a, b = UAsset(orig), UAsset(new)
    if b.names[:len(a.names)] != a.names or b.imports != a.imports:
        raise PatchError('validation: name/import tables changed unexpectedly')
    if len(a.exports) != len(b.exports):
        raise PatchError('validation: export count changed')
    if b.exports[0]['off'] != b.hdr_size or max(e['off'] + e['size'] for e in b.exports) != len(b.d) - 4:
        raise PatchError('validation: export layout inconsistent')
    if b.d[-4:] != a.d[-4:]:
        raise PatchError('validation: file tail changed')
    for ea, eb in zip(a.exports, b.exports):
        if (ea['name'], ea['cls'], ea['outer'], ea['sup']) != (eb['name'], eb['cls'], eb['outer'], eb['sup']):
            raise PatchError('validation: export identity changed')
        try:
            pa, _ = a.props(ea); pb, _ = b.props(eb)
        except PatchError:
            if a.d[ea['off']:ea['off'] + ea['size']] != b.d[eb['off']:eb['off'] + eb['size']]:
                raise PatchError(f'validation: unparseable export {ea["name"]} changed')
            continue
        da = {p[0]: p[1:4] for p in pa}; db = {p[0]: p[1:4] for p in pb}
        if [p[0] for p in pa if p[0] not in ALLOWED] != [p[0] for p in pb if p[0] not in ALLOWED]:
            raise PatchError(f'validation: property set changed in {ea["name"]}')
        for k in set(da) | set(db):
            if da.get(k) != db.get(k) and k not in ALLOWED:
                raise PatchError(f'validation: {k} changed in {ea["name"]}')
    if not check_state(new, rule):
        raise PatchError('validation: end state is not as intended')


def find_files(mods_dir):
    out = []
    for fname, (mid, rule) in RULES.items():
        hit = None
        for r, _, fs in os.walk(os.path.join(mods_dir, mid)):
            if fname in fs:
                hit = os.path.join(r, fname)
                break
        out.append((fname, mid, hit, rule))
    return out


def main():
    ap = argparse.ArgumentParser(description='Idempotent kaiju alliance/enemy patch.')
    ap.add_argument('--mods-dir', default=DEFAULT_MODS_DIR)
    ap.add_argument('--backup-dir', default=DEFAULT_BACKUP_DIR)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--force', action='store_true', help='skip the server-running check')
    args = ap.parse_args()
    mods_dir = os.path.abspath(args.mods_dir)
    if not os.path.isdir(mods_dir):
        sys.exit(f'Mods directory not found: {mods_dir}')
    if server_running() and not args.dry_run and not args.force:
        sys.exit('ARK SERVER IS RUNNING - not touching any files.\n'
                 'Shut the server down first (this script will not stop it), run again, then start it.')
    changed = current = pending = errors = 0
    for fname, mid, path, rule in find_files(mods_dir):
        if not path:
            print(f'[skip ] {fname}: mod {mid} not found'); continue
        rel = os.path.relpath(path, mods_dir)
        try:
            if args.restore:
                bk = os.path.join(args.backup_dir, rel)
                if os.path.exists(bk):
                    if not args.dry_run: atomic_write(path, open(bk, 'rb').read())
                    print(f'[restore] {rel}'); changed += 1
                continue
            orig = open(path, 'rb').read()
            new, log = build(orig, rule)
            if not log:
                if not check_state(orig, rule): raise PatchError('nothing to change but state is wrong')
                print(f'[ok   ] {rel}: already in place'); current += 1; continue
            validate(orig, new, rule)
            if args.dry_run:
                print(f'[would] {rel}'); pending += 1
            else:
                bk = os.path.join(args.backup_dir, rel)
                if not os.path.exists(bk):
                    os.makedirs(os.path.dirname(bk), exist_ok=True); shutil.copy2(path, bk)
                atomic_write(path, new)
                validate(orig, open(path, 'rb').read(), rule)
                print(f'[patch] {rel}'); changed += 1
            for l in log: print(f'          {l}')
        except (PatchError, OSError) as e:
            print(f'[ERROR] {rel}: {e}'); errors += 1
    print(f'\n{changed} changed, {pending} pending, {current} already in place, {errors} error(s).')
    if not args.dry_run:
        print('>>> Start the server again for this to take effect (this script never starts/stops it).')
    if errors: sys.exit(1)
    if args.dry_run and pending: sys.exit(2)


if __name__ == '__main__':
    main()
