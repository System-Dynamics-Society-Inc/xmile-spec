#!/usr/bin/env python3
"""Split the v1.0 -> v1.1 difference into ordered, semantic change sets.

Both endpoints come out of docx2adoc.py, so the difference between them is
pure content: no conversion noise. This script partitions that difference by
meaning -- one group per feature added or removed -- so the migration can be
committed as a readable sequence rather than a single opaque "apply v1.1".

Applying every group reproduces the generated v1.1 tree exactly; the caller
verifies that before tagging.

Usage:
    build_history.py V10DIR V11DIR --list
    build_history.py V10DIR V11DIR --apply GROUP[,GROUP...] --out DIR
"""

from __future__ import annotations

import argparse
import difflib
import glob
import os
import re
import shutil
import sys

# Ordered: features arrive before removals, and the release/rebrand lands last
# so the tagged commit is the version bump itself.
GROUPS = [
    ('subranges', 'Add array sub-range support'),
    ('knobs', 'Remove knobs; sliders become the sole slider-style input'),
    ('switches', 'Remove switches and option groups'),
    ('numeric-inputs', 'Remove numeric inputs and list input devices'),
    ('graphical-inputs', 'Remove graphical inputs'),
    ('lamps-gauges', 'Remove lamps and gauges'),
    ('graphics-frames', 'Remove graphics frames'),
    ('buttons', 'Remove buttons'),
    ('rebrand', 'Rebrand to SDS and bump the specification to version 1.1'),
]
GROUP_ORDER = {name: i for i, (name, _) in enumerate(GROUPS)}

# Sections withdrawn in 1.1, and the change set each belongs to. References to
# these anchors break in the same commit that removes the section.
SECTION_GROUPS = {
    'sec-sliders-and-knobs': 'knobs',
    'sec-switches-and-radio-buttons-option-groups': 'switches',
    'sec-numeric-inputs-and-list-input-devices': 'numeric-inputs',
    'sec-graphical-inputs': 'graphical-inputs',
    'sec-lamps-and-gauges': 'lamps-gauges',
    'sec-graphics-frames': 'graphics-frames',
    'sec-buttons': 'buttons',
}

# Sections that survive 1.1 but change number, so a hard-typed reference such
# as "Section 6.4.3" silently lands on different content. Each is attributed
# to the change that shifted the numbering: removing Lamps and Gauges pushed
# Graphs and Tables up one, and inserting Sub Ranges pushed the array
# subsections down one.
RENUMBERED_GROUPS = {
    'sec-graphs': 'lamps-gauges',
    'sec-tables': 'lamps-gauges',
    'sec-sub-ranges': 'subranges',
    'sec-array-operations': 'subranges',
    'sec-array-slicing': 'subranges',
    'sec-array-built-in-functions': 'subranges',
}

# Files whose entire difference belongs to one group.
FILE_GROUPS = {
    '00-front-matter.adoc': 'rebrand',
    'xmile.adoc': 'rebrand',
    '02-overall-structure.adoc': 'rebrand',
    '03-model-equation-structure.adoc': 'subranges',
}


def read_lines(path: str) -> list[str]:
    with open(path, encoding='utf-8') as fh:
        return fh.read().split('\n')


def anchor_at_line(lines: list[str]) -> list[str]:
    """Map each line index to the anchor of the section containing it."""
    current = ''
    out = []
    for line in lines:
        m = re.match(r'^\[\[(sec-[^\]]+)\]\]$', line)
        if m:
            current = m.group(1)
        out.append(current)
    return out


def classify(name: str, old: list[str], new: list[str],
             i1: int, i2: int, j1: int, j2: int,
             anchors: list[str]) -> str:
    if name in FILE_GROUPS:
        return FILE_GROUPS[name]

    changed = old[i1:i2] + new[j1:j2]

    # A cross-reference to a withdrawn section belongs with that removal, even
    # when the line itself lives in a surviving chapter.
    for anchor, group in SECTION_GROUPS.items():
        if any('<<%s>>' % anchor in line for line in changed):
            return group

    # A reference that merely moved to a renumbered section belongs with the
    # change that caused the shift.
    for anchor, group in RENUMBERED_GROUPS.items():
        if any('<<%s>>' % anchor in line for line in changed):
            return group

    # Otherwise attribute the hunk to the section it sits in.
    containing = anchors[i1] if i1 < len(anchors) else (anchors[-1] if anchors else '')
    if containing in SECTION_GROUPS:
        return SECTION_GROUPS[containing]

    # Deleting a section starts at its own anchor line, so look just ahead.
    for line in old[i1:i2]:
        m = re.match(r'^\[\[(sec-[^\]]+)\]\]$', line)
        if m and m.group(1) in SECTION_GROUPS:
            return SECTION_GROUPS[m.group(1)]

    raise SystemExit(
        'unclassified change in %s at line %d:\n%s'
        % (name, i1 + 1, '\n'.join(changed[:6])))


def plan(v10: str, v11: str):
    """Return {filename: [(opcode, group), ...]} plus the raw line lists."""
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(v10, '*.adoc')))
    files += [os.path.basename(p) for p in glob.glob(os.path.join(v11, '*.adoc'))
              if os.path.basename(p) not in files]

    result = {}
    for name in sorted(set(files)):
        old_path, new_path = os.path.join(v10, name), os.path.join(v11, name)
        old = read_lines(old_path) if os.path.exists(old_path) else []
        new = read_lines(new_path) if os.path.exists(new_path) else []
        anchors = anchor_at_line(old)
        ops = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, old, new, autojunk=False).get_opcodes():
            group = None
            if tag != 'equal':
                group = classify(name, old, new, i1, i2, j1, j2, anchors)
            ops.append((tag, i1, i2, j1, j2, group))
        result[name] = (old, new, ops)
    return result


def materialize(planned, applied: set[str], outdir: str, source_v10: str):
    os.makedirs(outdir, exist_ok=True)
    for name, (old, new, ops) in planned.items():
        lines = []
        for tag, i1, i2, j1, j2, group in ops:
            if tag == 'equal' or (group in applied):
                lines.extend(new[j1:j2])
            else:
                lines.extend(old[i1:i2])
        with open(os.path.join(outdir, name), 'w', encoding='utf-8',
                  newline='\n') as fh:
            fh.write('\n'.join(lines))

    images_src = os.path.join(source_v10, 'images')
    if os.path.isdir(images_src):
        images_dst = os.path.join(outdir, 'images')
        os.makedirs(images_dst, exist_ok=True)
        for img in glob.glob(os.path.join(images_src, '*')):
            shutil.copy2(img, images_dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('v10')
    ap.add_argument('v11')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--apply', default='')
    ap.add_argument('--out')
    args = ap.parse_args()

    planned = plan(args.v10, args.v11)

    if args.list:
        counts = {}
        for name, (old, new, ops) in planned.items():
            for tag, i1, i2, j1, j2, group in ops:
                if group:
                    entry = counts.setdefault(group, {})
                    entry[name] = entry.get(name, 0) + (i2 - i1) + (j2 - j1)
        for key, title in GROUPS:
            files = counts.get(key, {})
            total = sum(files.values())
            print('%-18s %4d lines  %s' % (key, total, title))
            for name in sorted(files):
                print('%22s %4d  %s' % ('', files[name], name))
        unknown = set(counts) - set(GROUP_ORDER)
        if unknown:
            print('UNEXPECTED GROUPS:', unknown)
        return 0

    if not args.out:
        ap.error('--out is required with --apply')
    applied = {g for g in args.apply.split(',') if g}
    unknown = applied - set(GROUP_ORDER)
    if unknown:
        ap.error('unknown group(s): %s' % ', '.join(sorted(unknown)))
    materialize(planned, applied, args.out, args.v10)
    print('wrote %s with groups: %s'
          % (args.out, ', '.join(sorted(applied, key=GROUP_ORDER.get)) or '(none)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
