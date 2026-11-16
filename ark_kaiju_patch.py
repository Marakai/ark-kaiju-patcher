#!/usr/bin/env python3
"""
ark_kaiju_patch.py - idempotent visibility patch for the ARK: Survival Evolved kaiju mods.

What it does (per kaiju creature blueprint, *_Character_BP.uasset):
  * bUseNetworkSpatialization  -> inserted as False where the blueprint doesn't set it
                                  (present+True is flipped to False)
  * NetCullDistanceSquared     -> raised to --netcull-m metres (default 5000 = Ghidorah level),
                                  inserted if missing
  * NetCullDistanceSquaredDormant -> raised likewise, only where already present
  * LDMaxDrawDistance / CachedMaxDrawDistance on CharacterMesh0 -> raised to --draw-m metres
                                  (default 2500), only where already present

Why: the server only sends a creature to a client while it is "relevant". These blueprints
shipped with missing or small relevance limits, so big kaiju pop in right next to you.
Net cull + spatialization are SERVER-side, so patching the server's mod files is enough.
Draw distance is CLIENT-side; use --export-dir to collect the patched files for client copies.

Safe to run repeatedly: files already in the desired state are left untouched (and reported).
Each file is validated after patching, written atomically, and the first original is backed up.

Colours are NOT handled - the kaiju have no colour-region masks; that can't be patched.

Usage:
  ark_kaiju_patch.py                    apply (refuses if the ARK server is running)
  ark_kaiju_patch.py --dry-run          show what would change; exit 2 if anything is pending
  ark_kaiju_patch.py --restore          put the backed-up originals back
  ark_kaiju_patch.py --export-dir DIR   also copy patched files (mod-id/relative path) to DIR
  ark_kaiju_patch.py --netcull-m 3000 --exact   set exactly 3 km (allows lowering)

Exit codes: 0 ok/up to date, 1 error on at least one file, 2 changes pending (--dry-run).
Python 3, standard library only.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile

DEFAULT_MODS_DIR = '/home/arkserver/ARK/ShooterGame/Content/Mods'
DEFAULT_BACKUP_DIR = os.path.expanduser('~/ARKModtests/auto-backups')

KAIJU_MODS = {
    '2474998329': 'Kaiju (Muto)',
    '2489532823': 'Mothra',
    '2514065085': 'Godzilla pack (Godzilla, Ghidorah, Rodan, Evolved, Mecha)',
    '2635615490': 'Super Godzilla',
    '2667389170': 'Pacific Rim Kaiju (Raijin)',
    '2832142815': 'Shin Godzilla',
    '2917197925': 'Gigan',
    '2921712374': 'Hedorah',
    '2923407686': 'Kiryu',
}
# The only properties this script is allowed to change (checked after every patch).
ALLOWED_CHANGES = {'NetCullDistanceSquared', 'NetCullDistanceSquaredDormant',
                   'bUseNetworkSpatialization', 'LDMaxDrawDistance', 'CachedMaxDrawDistance'}


class PatchError(Exception):
    pass


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


# --------------------------------------------------------------------------- uasset parsing
class UAsset:
    """Minimal reader for ASE-era cooked .uasset files (UE4 file version 404, single-file)."""

    def __init__(self, data):
        d = self.d = bytes(data)
        try:
            u32 = lambda o: struct.unpack_from('<I', d, o)[0]
            i32 = lambda o: struct.unpack_from('<i', d, o)[0]
            if u32(0) != 0x9E2A83C1:
                raise PatchError('not a uasset (bad magic)')
            if i32(4) != -3 or i32(12) != 404:
                raise PatchError(f'unexpected file version (legacy {i32(4)}, ue4 {i32(12)}); '
                                 'mod may have been rebuilt - not touching it')
            self.hdr_size = u32(24)
            p = 28
            n = i32(p); p += 4 + (n if n > 0 else -2 * n)
            p += 4  # package flags
            (self.name_count, self.name_off, self.exp_count, self.exp_off,
             self.imp_count, self.imp_off, self.dep_off) = struct.unpack_from('<7I', d, p)
            o = self.name_off
            self.names = []
            for _ in range(self.name_count):
                n = i32(o); o += 4
                if n > 0:
                    self.names.append(d[o:o + n - 1].decode('latin1')); o += n
                else:
                    self.names.append(d[o:o - 2 * n - 2].decode('utf-16le')); o += -2 * n
            self.name_end = o
            self.imports = []
            o = self.imp_off
            for _ in range(self.imp_count):
                cl = i32(o + 8)
                nm = i32(o + 20)
                outer = i32(o + 16)
                self.imports.append((self.names[cl], self.names[nm], outer))
                o += 28
            self.exports = []
            esz = (self.dep_off - self.exp_off) // self.exp_count
            if esz * self.exp_count != self.dep_off - self.exp_off:
                raise PatchError('export table size is not a whole number of entries')
            o = self.exp_off
            for _ in range(self.exp_count):
                cls, sup, outer, nm = struct.unpack_from('<iiii', d, o)
                size, off = struct.unpack_from('<ii', d, o + 24)
                self.exports.append(dict(cls=cls, sup=sup, outer=outer, name=self.names[nm],
                                         size=size, off=off, entry=o))
                o += esz
            self.esz = esz
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            raise PatchError(f'could not parse header: {e}')

    def objname(self, idx):
        if idx == 0: return 'None'
        if idx < 0: return self.imports[-idx - 1][1]
        return self.exports[idx - 1]['name']

    def props(self, ex):
        """Tagged properties of an export: list of (name, type, value, size, value_offset)."""
        d = self.d
        o = ex['off']; end = o + ex['size']
        out = []
        while o <= end - 8:
            ni, _ = struct.unpack_from('<ii', d, o)
            if ni < 0 or ni >= len(self.names):
                raise PatchError('bad property name index')
            name = self.names[ni]
            if name == 'None':
                return out, o
            o += 8
            ti, _ = struct.unpack_from('<ii', d, o); o += 8
            typ = self.names[ti]
            size, _ = struct.unpack_from('<ii', d, o); o += 8
            val = None
            if typ in ('StructProperty', 'ByteProperty', 'ArrayProperty'):
                o += 8
            elif typ == 'BoolProperty':
                val = bool(d[o]); o += 1
            data = d[o:o + size]
            if typ == 'FloatProperty' and size == 4: val = struct.unpack('<f', data)[0]
            elif typ == 'IntProperty' and size == 4: val = struct.unpack('<i', data)[0]
            elif typ == 'ObjectProperty' and size == 4: val = self.objname(struct.unpack('<i', data)[0])
            elif typ == 'NameProperty' and size == 8: val = self.names[struct.unpack('<i', data[:4])[0]]
            out.append((name, typ, val, size, o if typ != 'BoolProperty' else o - 1))
            o += size
        raise PatchError('property list has no terminator')

    def default_export(self):
        c = [e for e in self.exports if e['name'].startswith('Default__') and e['name'].endswith('_C')]
        if len(c) != 1:
            raise PatchError(f'expected exactly one Default__*_C export, found {len(c)}')
        return c[0]

    def mesh_export(self):
        c = [e for e in self.exports if e['name'] == 'CharacterMesh0']
        return c[0] if c else None


# --------------------------------------------------------------------------- patching
def build_patch(data, netcull_uu2, draw_uu, exact):
    """Return (new_bytes, log). new_bytes == data when nothing needs to change."""
    a = UAsset(data)
    d = bytearray(a.d)
    L = len(d)
    log = []
    new_names = []
    insertions = []   # (pos, bytes, export)

    def name_index(s):
        if s in a.names: return a.names.index(s)
        if s not in new_names: new_names.append(s)
        return a.name_count + new_names.index(s)

    def find(ex, pname):
        props, term = a.props(ex)
        for p in props:
            if p[0] == pname:
                return p, term
        return None, term

    def set_float(ex, pname, target, insert_if_missing):
        target = f32(target)
        p, term = find(ex, pname)
        if p:
            if p[1] != 'FloatProperty' or p[3] != 4:
                raise PatchError(f'{pname} is not a float property')
            cur = p[2]
            if (cur >= target) if not exact else (cur == target):
                return
            struct.pack_into('<f', d, p[4], target)
            log.append(f'{pname}: {cur:g} -> {target:g}')
        elif insert_if_missing:
            insertions.append((term, struct.pack('<iiiiiif', name_index(pname), 0,
                                                 name_index('FloatProperty'), 0, 4, 0, target), ex))
            log.append(f'{pname}: (unset) -> {target:g} [inserted]')

    def set_bool_false(ex, pname):
        p, term = find(ex, pname)
        if p:
            if p[1] != 'BoolProperty':
                raise PatchError(f'{pname} is not a bool property')
            if p[2] is True:
                d[p[4]] = 0
                log.append(f'{pname}: True -> False')
            return
        insertions.append((term, struct.pack('<iiiiiiB', name_index(pname), 0,
                                             name_index('BoolProperty'), 0, 0, 0, 0), ex))
        log.append(f'{pname}: (unset) -> False [inserted]')

    de = a.default_export()
    set_bool_false(de, 'bUseNetworkSpatialization')
    set_float(de, 'NetCullDistanceSquared', netcull_uu2, True)
    dormant, _ = find(de, 'NetCullDistanceSquaredDormant')
    if dormant:
        set_float(de, 'NetCullDistanceSquaredDormant', netcull_uu2, False)
    me = a.mesh_export()
    if me:
        for pn in ('LDMaxDrawDistance', 'CachedMaxDrawDistance'):
            set_float(me, pn, draw_uu, False)

    if not log:
        return bytes(a.d), []

    if insertions or new_names:
        nb = b''
        for s in new_names:
            e = s.encode('latin1') + b'\0'
            nb += struct.pack('<i', len(e)) + e
        S, k = len(nb), len(new_names)
        total_ins = sum(len(b) for _, b, _ in insertions)

        def u32(o): return struct.unpack_from('<I', d, o)[0]
        def w32(o, v): struct.pack_into('<I', d, o, v)
        # every absolute offset we are about to move must be where we expect it
        expect = {41: a.name_count, 105: a.name_count, 24: a.hdr_size, 53: a.exp_off,
                  61: a.imp_off, 65: a.dep_off, 73: a.hdr_size - 4, 151: a.hdr_size - 4, 155: L - 4}
        for pos, val in expect.items():
            if u32(pos) != val:
                raise PatchError(f'header layout differs from expectation at byte {pos}')
        if a.name_end != a.imp_off:
            raise PatchError('name table is not directly followed by the import table')
        if a.exports[0]['off'] != a.hdr_size or \
           max(e['off'] + e['size'] for e in a.exports) != L - 4:
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


def validate(orig, new, netcull_uu2, draw_uu, exact):
    """Re-parse the result; ensure only allowed properties changed and the end state is right."""
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
        if [p[0] for p in pa if p[0] not in ALLOWED_CHANGES] != [p[0] for p in pb if p[0] not in ALLOWED_CHANGES]:
            raise PatchError(f'validation: property set changed in {ea["name"]}')
        for k in set(da) | set(db):
            if da.get(k) != db.get(k) and k not in ALLOWED_CHANGES:
                raise PatchError(f'validation: {k} changed in {ea["name"]}')
    state = describe(b, netcull_uu2, draw_uu, exact)
    if not state['ok']:
        raise PatchError('validation: end state not as intended: ' + state['why'])


def describe(a, netcull_uu2, draw_uu, exact):
    """Is this asset already in the desired state?"""
    if isinstance(a, (bytes, bytearray)):
        a = UAsset(a)
    why = []
    props = {p[0]: p for p in a.props(a.default_export())[0]}
    sp = props.get('bUseNetworkSpatialization')
    if not sp or sp[2] is not False: why.append('bUseNetworkSpatialization not False')
    nc = props.get('NetCullDistanceSquared'); t = f32(netcull_uu2)
    if not nc or not ((nc[2] == t) if exact else (nc[2] >= t)): why.append('NetCullDistanceSquared')
    dm = props.get('NetCullDistanceSquaredDormant')
    if dm and not ((dm[2] == t) if exact else (dm[2] >= t)): why.append('NetCullDistanceSquaredDormant')
    me = a.mesh_export()
    if me:
        for p in a.props(me)[0]:
            if p[0] in ('LDMaxDrawDistance', 'CachedMaxDrawDistance'):
                tt = f32(draw_uu)
                if not ((p[2] == tt) if exact else (p[2] >= tt)): why.append(p[0])
    return {'ok': not why, 'why': ', '.join(why)}


# --------------------------------------------------------------------------- file handling
def find_blueprints(mods_dir, only_mods):
    found = []
    for mid in only_mods:
        root = os.path.join(mods_dir, mid)
        if not os.path.isdir(root):
            found.append((mid, None))
            continue
        for r, _, fs in os.walk(root):
            for f in sorted(fs):
                if f.endswith('_Character_BP.uasset'):
                    found.append((mid, os.path.join(r, f)))
    return found


def server_running():
    try:
        for pid in os.listdir('/proc'):
            if pid.isdigit():
                try:
                    argv0 = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')[0]
                except OSError:
                    continue
                # match the program name only, so a shell/ssh/grep that merely mentions
                # "ShooterGameServer" in its arguments is not mistaken for the server
                if os.path.basename(argv0) == b'ShooterGameServer':
                    return True
    except OSError:
        pass
    return False


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.patch-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp): os.unlink(tmp)
        raise


def main():
    ap = argparse.ArgumentParser(description='Idempotent visibility patch for ASE kaiju mods.',
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('Usage:')[1])
    ap.add_argument('--mods-dir', default=DEFAULT_MODS_DIR, help='ShooterGame/Content/Mods directory')
    ap.add_argument('--backup-dir', default=DEFAULT_BACKUP_DIR, help='where first-time backups of changed files go')
    ap.add_argument('--netcull-m', type=float, default=5000.0, help='net cull / relevance distance in metres (default 5000)')
    ap.add_argument('--draw-m', type=float, default=2500.0, help='mesh max draw distance in metres (default 2500)')
    ap.add_argument('--exact', action='store_true', help='set values exactly instead of only raising them')
    ap.add_argument('--dry-run', action='store_true', help='report only; exit 2 if changes are pending')
    ap.add_argument('--restore', action='store_true', help='restore backed-up originals and exit')
    ap.add_argument('--export-dir', help='copy patched files here (modid/relative path) for client installs')
    ap.add_argument('--force', action='store_true', help='skip the server-running check')
    args = ap.parse_args()

    mods_dir = os.path.abspath(args.mods_dir)
    if not os.path.isdir(mods_dir):
        sys.exit(f'Mods directory not found: {mods_dir}')
    netcull_uu2 = (args.netcull_m * 100.0) ** 2
    draw_uu = args.draw_m * 100.0

    running = server_running()
    if running and args.dry_run:
        print('NOTE: an ARK server process is running. A dry-run is read-only and safe, but a real\n'
              '      run needs the server shut down first.\n')
    elif running and not args.force:
        sys.exit('ARK SERVER IS RUNNING - not touching any files.\n'
                 'Please shut the server down first (this script will NOT stop it for you).\n'
                 'Reason: a running server has these files loaded in memory and could write\n'
                 'stale data back over the patched ones, or load a half-written file.\n'
                 'Stop the server, run this again, then start the server.\n'
                 '(--dry-run shows what would change without writing; --force overrides this check.)')
    elif running and args.force:
        print('WARNING: an ARK server is running and --force was given. Files will be changed\n'
              '         underneath it; stop and restart the server afterwards, and expect the\n'
              '         running instance to keep the OLD values until it is restarted.\n')

    files = find_blueprints(mods_dir, KAIJU_MODS)
    changed = current = errors = pending = missing = 0

    for mid, path in files:
        if path is None:
            print(f'[skip ] mod {mid} ({KAIJU_MODS[mid]}) not installed')
            missing += 1
            continue
        rel = os.path.relpath(path, mods_dir)
        label = f'{rel}'
        try:
            if args.restore:
                bk = os.path.join(args.backup_dir, rel)
                if os.path.exists(bk):
                    if not args.dry_run:
                        atomic_write(path, open(bk, 'rb').read())
                    print(f'[restore] {label}')
                    changed += 1
                continue
            orig = open(path, 'rb').read()
            new, log = build_patch(orig, netcull_uu2, draw_uu, args.exact)
            if not log:
                state = describe(orig, netcull_uu2, draw_uu, args.exact)
                if not state['ok']:
                    raise PatchError('nothing to patch but state is wrong: ' + state['why'])
                print(f'[ok   ] {label}: already patched')
                current += 1
            else:
                validate(orig, new, netcull_uu2, draw_uu, args.exact)
                if args.dry_run:
                    print(f'[would] {label}')
                    pending += 1
                else:
                    bk = os.path.join(args.backup_dir, rel)
                    if not os.path.exists(bk):
                        os.makedirs(os.path.dirname(bk), exist_ok=True)
                        shutil.copy2(path, bk)
                    atomic_write(path, new)
                    # read back what actually landed on disk
                    validate(orig, open(path, 'rb').read(), netcull_uu2, draw_uu, args.exact)
                    print(f'[patch] {label}')
                    changed += 1
                for l in log:
                    print(f'          {l}')
            if args.export_dir and not args.dry_run:
                out = os.path.join(args.export_dir, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copyfile(path, out)
        except (PatchError, OSError) as e:
            print(f'[ERROR] {label}: {e}')
            errors += 1

    print()
    if args.restore:
        print(f'Restored {changed} file(s).')
    else:
        print(f'{changed} patched, {pending} pending, {current} already up to date, '
              f'{missing} mod(s) not installed, {errors} error(s).')
        if args.export_dir and not args.dry_run:
            print(f'Patched files copied to {args.export_dir} (client use: copy into <ARK>/ShooterGame/Content/Mods/).')
    if not args.dry_run:
        if changed:
            print('>>> The server needs to be STARTED again for these changes to take effect.')
        else:
            print('>>> No files were changed. If you shut the server down for this run, start it again.')
        print('    (This script never starts or stops the server itself.)')
    if errors:
        sys.exit(1)
    if args.dry_run and pending:
        sys.exit(2)


if __name__ == '__main__':
    main()
