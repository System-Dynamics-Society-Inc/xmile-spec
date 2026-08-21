"""Produce a redline of the specification for review.

The Technical Committee reviewed 1.0 and 1.1 as Word "Compare Documents"
output: the whole document, with insertions and deletions marked in place. A
git diff is not that. It shows AsciiDoc source, splits a paragraph across hunks,
and says nothing about which numbered section a change lands in.

This renders both revisions through Asciidoctor first, then compares the
rendered prose. Working from the rendered form means cross-references appear as
the section numbers a reviewer will cite, and markup noise never reaches the
page.

Output is one self-contained HTML file: a summary, then every changed block
grouped under the section it belongs to, with word-level marking inside changed
paragraphs. Unchanged material is omitted rather than reproduced, so the
reviewer reads only what moved.

Both sides are named explicitly. --base is what you compare against and
--target is what you compare; --target defaults to HEAD, meaning the working
tree, which is what you want when reviewing your own branch and not what you
want when rendering one historical revision against another.

Usage:
  python tools/redline.py --base origin/main
  python tools/redline.py --base v1.1 --target origin/main
  python tools/redline.py --base v1.1 --out build/redline.html
"""
import argparse
import collections
import difflib
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser

BLOCK_TAGS = {'p', 'pre'}
HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


class Block:
    __slots__ = ('kind', 'text', 'section', 'level')

    def __init__(self, kind, text, section, level=0):
        self.kind = kind          # 'heading', 'para' or 'code'
        self.text = text
        self.section = section    # heading trail, most specific last
        self.level = level

    @property
    def title(self):
        """A heading's text without the number Asciidoctor assigned to it."""
        return HEAD_NUMBER.sub('', ' '.join(self.text.split()))

    @property
    def key(self):
        # Headings align on their title, not their numbered form. Inserting one
        # section shifts every number after it, and aligning on the numbered
        # text pairs the new "2.3. AI Information Section" against the old
        # "2.3. Model Simulation Specification Section" purely because they
        # share a position. Renumbered headings are reported separately.
        if self.kind == 'heading':
            return 'heading\x00%s' % self.title
        return '%s\x00%s' % (self.kind, ' '.join(self.text.split()))


class Extractor(HTMLParser):
    """Collect headings, paragraphs and listing blocks in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.trail = []           # (level, text)
        self._stack = []          # open capture tags
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in HEADING_TAGS or tag in BLOCK_TAGS:
            if not self._stack:
                self._buf = []
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if not self._stack or tag not in HEADING_TAGS | BLOCK_TAGS:
            return
        # Unwind to the matching tag; asciidoctor nests <p> inside <li>.
        while self._stack and self._stack[-1] != tag:
            self._stack.pop()
        if self._stack:
            self._stack.pop()
        if self._stack:
            return
        text = ''.join(self._buf).strip()
        self._buf = []
        if not text:
            return
        if tag in HEADING_TAGS:
            level = int(tag[1])
            while self.trail and self.trail[-1][0] >= level:
                self.trail.pop()
            self.trail.append((level, text))
            self.blocks.append(Block('heading', text, tuple(t for _, t in self.trail), level))
        else:
            kind = 'code' if tag == 'pre' else 'para'
            self.blocks.append(Block(kind, text, tuple(t for _, t in self.trail)))

    def handle_data(self, data):
        if self._stack:
            self._buf.append(data)


def extract(path):
    parser = Extractor()
    with open(path, encoding='utf-8') as handle:
        parser.feed(handle.read())
    return parser.blocks


# ------------------------------------------------------------------ diffing --

TOKENS = re.compile(r'\s+|\w+|[^\w\s]')


def word_diff(old, new):
    """Inline marking for a changed block."""
    a = TOKENS.findall(old)
    b = TOKENS.findall(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == 'equal':
            out.append(html.escape(''.join(b[j1:j2])))
        else:
            if i1 != i2:
                out.append('<del>%s</del>' % html.escape(''.join(a[i1:i2])))
            if j1 != j2:
                out.append('<ins>%s</ins>' % html.escape(''.join(b[j1:j2])))
    return ''.join(out)


def similar(a, b):
    return difflib.SequenceMatcher(a=a.text, b=b.text, autojunk=False).ratio()


# Asciidoctor numbers sections, footnotes and cross-references itself, so
# inserting one section renumbers everything after it. Those blocks differ, but
# no one edited them, and burying six real edits among forty automatic ones is
# how a reviewer misses the six. They are separated out rather than dropped:
# the renumbering is the evidence that every reference followed its anchor,
# which is precisely what went wrong between 1.0 and 1.1.
HEAD_NUMBER = re.compile(r'^\d+(?:\.\d+)*\.\s*')
FOOTNOTE = re.compile(r'\[\d+\]')
XREF = re.compile(r'Section\s+\d+(?:\.\d+)*')


def numbering_only(old, new):
    """True when the two blocks differ solely by automatic numbering."""
    def normalise(block):
        text = ' '.join(block.text.split())
        if block.kind == 'heading':
            text = HEAD_NUMBER.sub('', text)
        text = FOOTNOTE.sub('[#]', text)
        return XREF.sub('Section #', text)
    return normalise(old) == normalise(new)


def heading_renumbering(old_blocks, new_blocks):
    """Headings that kept their title but changed number.

    These do not come out of the block alignment, because headings align on
    their title so that an inserted section does not appear to rename its
    neighbour. The number change is still worth listing.

    Eighteen titles occur more than once in the specification, "Arrays" three
    times, so a title is not a key. Keeping one heading per title paired the nth
    "Arrays" against whichever came last and reported a renumbering for each,
    which showed up as nineteen renumbered headings in a comparison of two
    identical documents. Occurrences are matched in order instead.
    """
    buckets = collections.defaultdict(list)
    for block in old_blocks:
        if block.kind == 'heading':
            buckets[block.title].append(block)

    used = collections.Counter()
    out = []
    for new in new_blocks:
        if new.kind != 'heading':
            continue
        bucket = buckets.get(new.title)
        if not bucket:
            continue
        index = used[new.title]
        used[new.title] += 1
        if index >= len(bucket):
            continue
        old = bucket[index]
        if ' '.join(old.text.split()) != ' '.join(new.text.split()):
            out.append(('renumbered', old, new))
    return out


def changes(old_blocks, new_blocks):
    """Yield (op, old_block, new_block) for everything that is not identical."""
    matcher = difflib.SequenceMatcher(
        a=[b.key for b in old_blocks], b=[b.key for b in new_blocks], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            continue
        if op == 'delete':
            for b in old_blocks[i1:i2]:
                yield ('removed', b, None)
        elif op == 'insert':
            for b in new_blocks[j1:j2]:
                yield ('added', None, b)
        else:
            # Pair up blocks that are recognisably the same prose reworded, and
            # treat the rest as plain additions and removals. Without this a
            # reworded sentence shows as an unrelated delete plus insert.
            olds, news = list(old_blocks[i1:i2]), list(new_blocks[j1:j2])
            used = set()
            for b in news:
                best, score = None, 0.0
                for index, a in enumerate(olds):
                    if index in used or a.kind != b.kind:
                        continue
                    ratio = similar(a, b)
                    if ratio > score:
                        best, score = index, ratio
                if best is not None and score >= 0.5:
                    used.add(best)
                    a = olds[best]
                    yield ('renumbered' if numbering_only(a, b) else 'changed', a, b)
                else:
                    yield ('added', None, b)
            for index, a in enumerate(olds):
                if index not in used:
                    yield ('removed', a, None)


# ------------------------------------------------------------------- render --

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#5b6570;--rule:#dcdfe3;
--ins-bg:#e6f5ea;--ins-fg:#0f5a2a;--del-bg:#fdeaea;--del-fg:#8a1220;--chip:#f2f4f6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:52rem;margin:0 auto;padding:2.5rem 1.25rem 6rem}
h1{font-size:1.6rem;line-height:1.25;margin:0 0 .35rem}
.sub{color:var(--muted);margin:0 0 2rem}
.meta{border:1px solid var(--rule);border-radius:8px;padding:1rem 1.1rem;margin:0 0 2rem;
background:#fafbfc;font-size:.9rem}
.meta dl{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1rem;margin:0}
.meta dt{color:var(--muted)}
.meta dd{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.tally{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 2.5rem}
.tally span{border:1px solid var(--rule);border-radius:999px;padding:.2rem .7rem;
font-size:.85rem;background:var(--chip)}
h2.sec{font-size:1.05rem;margin:2.5rem 0 .25rem;padding-top:1.25rem;
border-top:1px solid var(--rule)}
.crumb{color:var(--muted);font-size:.8rem;margin:0 0 1rem}
.blk{margin:0 0 1.1rem;padding:.7rem .9rem;border-left:3px solid var(--rule);
border-radius:0 6px 6px 0;background:#fcfcfd;overflow-x:auto}
.blk.added{border-left-color:#2f9e5f;background:var(--ins-bg)}
.blk.removed{border-left-color:#c0392b;background:var(--del-bg)}
.blk.changed{border-left-color:#b8860b;background:#fdf8e8}
.blk.renumbered{border-left-color:var(--rule);background:transparent;opacity:.75}
.tag{display:inline-block;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);margin-bottom:.35rem}
.blk p{margin:0}
pre{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-size:.82rem;white-space:pre-wrap;word-break:break-word}
ins{background:var(--ins-bg);color:var(--ins-fg);text-decoration:none;
box-shadow:inset 0 -.35em 0 rgba(47,158,95,.18)}
del{background:var(--del-bg);color:var(--del-fg)}
.note{border-left:3px solid #b8860b;background:#fdf8e8;padding:.8rem .95rem;
border-radius:0 6px 6px 0;margin:0 0 2rem;font-size:.92rem}
@media print{body{font-size:11pt}.wrap{max-width:none;padding:0}
.blk{break-inside:avoid}h2.sec{break-after:avoid}}
@media (prefers-color-scheme:dark){
:root{--bg:#16181c;--fg:#e6e8ea;--muted:#9aa4af;--rule:#2c3138;--chip:#22262c;
--ins-bg:#12301f;--ins-fg:#8fe0ab;--del-bg:#33161a;--del-fg:#ffb3ba}
.meta,.blk{background:#1b1e23}.blk.added{background:var(--ins-bg)}
.blk.removed{background:var(--del-bg)}.blk.changed{background:#2a2415}
.note{background:#2a2415}}
"""

LABEL = {'added': 'added', 'removed': 'removed', 'changed': 'changed',
         'renumbered': 'renumbered automatically'}


def render_block(op, old, new):
    block = new or old
    body = block.text
    if op == 'changed':
        inner = word_diff(old.text, new.text)
    elif op == 'added':
        inner = '<ins>%s</ins>' % html.escape(body)
    else:
        inner = '<del>%s</del>' % html.escape(body)
    tag = LABEL[op] + (' heading' if block.kind == 'heading' else
                       ' example' if block.kind == 'code' else '')
    wrapper = 'pre' if block.kind == 'code' else 'p'
    return ('<div class="blk %s"><span class="tag">%s</span><%s>%s</%s></div>'
            % (op, html.escape(tag), wrapper, inner, wrapper))


def build_page(entries, meta, title):
    tally = {'added': 0, 'removed': 0, 'changed': 0, 'renumbered': 0}
    for op, _o, _n in entries:
        tally[op] += 1

    editorial = [e for e in entries if e[0] != 'renumbered']
    mechanical = [e for e in entries if e[0] == 'renumbered']

    parts = ['<!doctype html>',
             '<html lang="en">',
             # Without a declared charset a browser sniffs, and the specification
             # prose is full of curly quotes and dashes that then arrive as
             # mojibake in a file opened straight off disk.
             '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>%s</title>' % html.escape(title),
             '<style>%s</style>' % CSS,
             '<div class="wrap">',
             '<h1>%s</h1>' % html.escape(title),
             '<p class="sub">Rendered redline of the specification prose. '
             'Unchanged material is omitted.</p>',
             '<div class="meta"><dl>']
    for term, value in meta:
        parts.append('<dt>%s</dt><dd>%s</dd>' % (html.escape(term), html.escape(value)))
    parts.append('</dl></div>')
    parts.append('<p class="tally"><span>%d added</span><span>%d changed</span>'
                 '<span>%d removed</span><span>%d renumbered automatically</span></p>'
                 % (tally['added'], tally['changed'], tally['removed'],
                    tally['renumbered']))
    parts.append('<div class="note"><strong>How to read this.</strong> Green marks new '
                 'text, red marks removed text, and amber marks a block that was '
                 'reworded, with the wording change shown inline. Blocks are grouped '
                 'under the numbered section they appear in, using the numbering of '
                 'the revised document. Blocks that differ only because Asciidoctor '
                 'renumbered a heading, a footnote or a cross-reference are held back '
                 'to the end, so that the editorial changes can be read on their '
                 'own.</div>')

    def emit(items):
        current = None
        for op, old, new in items:
            block = new or old
            section = block.section
            if section != current:
                current = section
                heading = section[-1] if section else 'Front matter'
                crumb = ' &rsaquo; '.join(html.escape(s) for s in section[:-1]) or '&nbsp;'
                parts.append('<h2 class="sec">%s</h2><p class="crumb">%s</p>'
                             % (html.escape(heading), crumb))
            parts.append(render_block(op, old, new))

    emit(editorial)

    if mechanical:
        parts.append('<h2 class="sec">Automatic renumbering</h2>')
        parts.append('<p class="crumb">%d block(s) whose only change is a number '
                     'Asciidoctor assigns. Nobody edited these. They are listed because '
                     'they are the evidence that every cross-reference followed its '
                     'anchor when a new section was inserted, which is the failure '
                     'recorded in the 1.1 errata.</p>' % len(mechanical))
        for op, old, new in mechanical:
            parts.append(render_block(op, old, new))

    parts.append('</div>')
    parts.append('</html>')
    return '\n'.join(parts)


# --------------------------------------------------------------------- main --

def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def git_describe(ref):
    out = run(['git', 'log', '-1', '--format=%h %s', ref]).stdout.strip()
    return out


class Checkout:
    """A worktree for `ref`, or the working tree when the ref is HEAD or absent.

    Both sides of the comparison need this. The revised side used to be the
    working tree unconditionally, which is right when reviewing your own branch
    and wrong for anything else: asked for main against the v1.1 tag it rendered
    the current branch against v1.1 instead, so a redline of the errata
    corrections carried the whole of the 1.2 work as well.
    """

    def __init__(self, ref, work, name):
        self.ref = ref
        self.work = work
        self.name = name
        self.tree = None

    def __enter__(self):
        if not self.ref or self.ref == 'HEAD':
            return '.'
        self.tree = os.path.join(self.work, self.name)
        run(['git', 'worktree', 'add', '--detach', self.tree, self.ref])
        return self.tree

    def __exit__(self, *exc):
        if self.tree:
            try:
                run(['git', 'worktree', 'remove', '--force', self.tree])
            except Exception:                                  # noqa: BLE001
                pass
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog='redline.py',
                                     description='Render a reviewable redline of the spec.')
    parser.add_argument('--base', default='origin/main',
                        help='revision to compare against (default: origin/main)')
    parser.add_argument('--target', default='HEAD',
                        help='revision to compare, and the one whose numbering the '
                             'report uses (default: HEAD, meaning the working tree)')
    parser.add_argument('--out', default='build/redline.html', help='output HTML file')
    parser.add_argument('--title', default=None, help='page title')
    parser.add_argument('--source', default='spec/xmile.adoc', help='master AsciiDoc file')
    args = parser.parse_args(argv)

    work = tempfile.mkdtemp(prefix='redline-')
    try:
        base_html = os.path.join(work, 'base.html')
        head_html = os.path.join(work, 'head.html')
        with Checkout(args.base, work, 'base') as base_tree, \
             Checkout(args.target, work, 'target') as head_tree:
            run(['node', 'tools/build.mjs', os.path.join(base_tree, args.source), base_html])
            run(['node', 'tools/build.mjs', os.path.join(head_tree, args.source), head_html])

        old_blocks = extract(base_html)
        new_blocks = extract(head_html)
        entries = list(changes(old_blocks, new_blocks))
        entries += heading_renumbering(old_blocks, new_blocks)

        base_title = next((b.text for b in old_blocks if b.kind == 'heading'), '')
        head_title = next((b.text for b in new_blocks if b.kind == 'heading'), '')
        title = args.title or ('Redline: %s to %s' % (
            base_title.replace('XML Interchange Language for System Dynamics (XMILE) ', ''),
            head_title.replace('XML Interchange Language for System Dynamics (XMILE) ', '')))

        meta = [
            ('Base', '%s (%s)' % (args.base, git_describe(args.base))),
            ('Revised', '%s (%s)' % (args.target, git_describe(args.target))),
            ('Base blocks', str(len(old_blocks))),
            ('Revised blocks', str(len(new_blocks))),
        ]
        page = build_page(entries, meta, title)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(page)
        print('redline OK -> %s (%d changed block(s) from %d, %d KiB)'
              % (args.out, len(entries), len(new_blocks),
                 os.path.getsize(args.out) // 1024))
        return 0
    finally:
        # Checkout removes its own worktrees; only the scratch directory is left.
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
