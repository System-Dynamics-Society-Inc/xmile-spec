#!/usr/bin/env python3
"""Convert the XMILE specification from Word (.docx) to AsciiDoc.

The v1.1 source is a Word "Compare Documents" redline, so a single file holds
both endpoints: rejecting every tracked change yields v1.0, accepting every
change yields v1.1. Converting both through this one code path is what keeps
the git diff between them free of conversion noise.

Usage:
    docx2adoc.py SOURCE.docx OUTDIR --mode {accept,reject} --version 1.1
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'

# Paragraph styles that carry a verbatim code sample rather than prose.
CODE_STYLES = {'code0', 'codefirst', 'codelast', 'code'}
CODE_STYLE_PREFIXES = ('MediumShading1-Accent1', 'MediumGrid1-Accent2')

# Auto-generated tables of contents; AsciiDoc rebuilds these from :toc:.
DROP_STYLES = ('TOC', 'TOCHeading', 'Index')

# Two figures are stored as a VML group -- an EMF base plus PNG shapes layered
# on top -- so no single part of the .docx contains the whole picture, and EMF
# will not render in a browser. tools/render-figures.ps1 flattens each group
# into one PNG, committed under spec/images/; referencing the EMF here would
# emit a broken image and drop the nested module boxes.
FIGURE_OVERRIDES = {
    'image3.emf': 'figure-submodel-scope-a-d.png',
    'image6.emf': 'figure-submodel-scope-a-x.png',
}

# Word numbered the media by order of appearance; name them for what they show
# so the spec source stays readable.
IMAGE_NAMES = {
    'image1.png': 'oasis-logo.png',
    'image2.png': 'figure-graphical-function-curve.png',
    'image9.png': 'figure-view-coordinate-system.png',
}

HEADING_STYLES = {
    'Heading1': 1, 'Heading2': 2, 'Heading3': 3,
    'Heading4': 4, 'Heading5': 5, 'Heading6': 6,
}


def is_code_style(style: str) -> bool:
    return style in CODE_STYLES or style.startswith(CODE_STYLE_PREFIXES)


def slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return s or 'section'


# --------------------------------------------------------------------------
# Inline text
# --------------------------------------------------------------------------

# Formatting marks only start a span in "constrained" position -- after the
# start of the line or whitespace/opening punctuation, and before a non-space.
# That is why an identifier like one_time_unit needs no escaping at all, while
# a phrase such as "_foo and bar_" does. Escaping everything is not the safe
# choice: a backslash that guards nothing is printed literally, so over-
# escaping puts "ship\_time" in the rendered output.
FORMATTING_MARKS = '_*`#~^+'
CONSTRAINED_PREFIX = '([{,;:"\''


def escape_inline(text: str) -> str:
    """Escape only marks that would really open a formatting span.

    All emphasis in the source comes from Word run properties, so every mark
    in the text itself is literal and must render as typed. A mark only needs
    a backslash when a partner exists to close the span -- "underscore (_)"
    has none, and escaping it there would print the backslash.
    """
    length = len(text)
    escape_at: set[int] = set()

    for mark in FORMATTING_MARKS:
        starts, ends, doubles = [], [], []
        i = 0
        while i < length:
            if text[i] != mark:
                i += 1
                continue
            prev = text[i - 1] if i else ''
            nxt = text[i + 1] if i + 1 < length else ''
            if nxt == mark and mark != '+':
                doubles.append(i)
                i += 2
                continue
            if (i == 0 or prev.isspace() or prev in CONSTRAINED_PREFIX) \
                    and nxt and not nxt.isspace():
                starts.append(i)
            if prev and not prev.isspace() \
                    and (i + 1 == length or not nxt.isalnum()):
                ends.append(i)
            i += 1

        # A span needs an opening mark with some later closing mark.
        if any(e > s for s in starts for e in ends):
            escape_at.update(starts)
        # Doubled marks are unconstrained: any two of them pair up.
        if len(doubles) >= 2:
            escape_at.update(doubles)

    if escape_at:
        text = ''.join(('\\' + c) if i in escape_at else c
                       for i, c in enumerate(text))
    # {name} would resolve as an attribute reference.
    return re.sub(r'\{(?=[A-Za-z0-9_-]+\})', '\\\\{', text)


# Where a run of literal metacharacters sits next to an emphasised run, the
# two collide: in the BNF grammar "[_digit_]*" the literal bracket is read as
# an inline attribute list and the literal star pairs with the emphasis marks.
# Escaping cannot fix this reliably, so such runs go through a passthrough,
# which still escapes < and & but performs no formatting substitutions.
COLLIDING_CHARS = set('*_+[]`{}#^~')


def needs_passthrough(text: str) -> bool:
    return any(c in COLLIDING_CHARS for c in text)


def passthrough(text: str) -> str:
    return 'pass:c[%s]' % text.replace(']', '\\]')


def render_code_span(text: str) -> str:
    """Monospace with inline substitutions switched off.

    A plain `sim_specs` still runs substitutions inside the backticks; wrapping
    the content in a passthrough fence is what keeps XML samples and
    identifiers verbatim.
    """
    fence = '+'
    while fence in text:
        fence += '+'
    ticks = '``' if '`' in text else '`'
    return '%s%s%s%s%s' % (ticks, fence, text, fence, ticks)


# A paragraph is prose, but AsciiDoc decides a line's block type from its first
# characters. The specification has prose that starts that way for real: the
# operator table line "=  <> Equality operators" parses as a level-0 heading,
# and the bibliography entry "[RFC2119] Bradner, S., ..." parses as a block
# attribute list, which silently swallows the entry.
LINE_START_HAZARD = re.compile(
    r'''^(?:
          =+[ \t]              # section title
        | \[[^\]]*\]           # block attribute list / anchor
        | \.[^\s.]             # block title
        | [*\-][ \t]           # list item
        | \d+\.[ \t]           # numbered list item
        | //                   # line comment
        | :[^\s:]+:            # attribute entry
        | \+[ \t]*$            # list continuation
        | (?:-{4,}|={4,}|\*{4,}|_{4,}|\.{4,}|/{4,})[ \t]*$   # block delimiter
        | <\d+>                # callout
        | \|                   # table cell
        | [A-Z]+:[ \t]         # admonition (NOTE:, WARNING:, ...)
    )''', re.VERBOSE)


def protect_line_start(text: str) -> str:
    """Stop a prose line from being reinterpreted as AsciiDoc block markup.

    {empty} resolves to nothing in the output, but it is present when the
    parser classifies the line, so the line stays a paragraph.
    """
    if LINE_START_HAZARD.match(text):
        return '{empty}' + text
    return text


class Run:
    __slots__ = ('text', 'code', 'bold', 'italic', 'link', 'footnote', 'image')

    def __init__(self, text='', code=False, bold=False, italic=False,
                 link=None, footnote=None, image=None):
        self.text = text
        self.code = code
        self.bold = bold
        self.italic = italic
        self.link = link
        self.footnote = footnote
        self.image = image


def render_runs(runs: list[Run]) -> str:
    """Merge adjacent runs with identical formatting, then emit AsciiDoc."""
    merged: list[Run] = []
    for r in runs:
        if (merged and not r.image and not r.footnote and not merged[-1].image
                and not merged[-1].footnote
                and merged[-1].code == r.code and merged[-1].bold == r.bold
                and merged[-1].italic == r.italic and merged[-1].link == r.link):
            merged[-1].text += r.text
        else:
            merged.append(Run(r.text, r.code, r.bold, r.italic, r.link,
                              r.footnote, r.image))

    formatted = [bool(r.code or r.bold or r.italic) for r in merged]

    out = []
    for index, r in enumerate(merged):
        if r.image:
            out.append('image:%s[]' % r.image)
            continue
        if r.footnote is not None:
            out.append('footnote:[%s]' % r.footnote)
            continue
        if not r.text:
            continue
        # Preserve leading/trailing spaces outside the formatting marks so
        # constrained AsciiDoc formatting still applies.
        lead = r.text[:len(r.text) - len(r.text.lstrip())]
        trail = r.text[len(r.text.rstrip()):]
        core = r.text.strip()
        if not core:
            out.append(r.text)
            continue

        if r.code:
            body = render_code_span(core)
        elif r.bold or r.italic:
            body = passthrough(core) if needs_passthrough(core) \
                else escape_inline(core)
            # Unconstrained marks so formatting still applies when the span
            # butts against a word, as in _N_th-order.
            if r.bold:
                body = '**%s**' % body
            if r.italic:
                body = '__%s__' % body
        else:
            beside_formatting = ((index and formatted[index - 1])
                                 or (index + 1 < len(merged)
                                     and formatted[index + 1]))
            body = passthrough(core) \
                if beside_formatting and needs_passthrough(core) \
                else escape_inline(core)
        if r.link:
            body = '%s[%s]' % (r.link, core if r.code else escape_inline(core))
        out.append(lead + body + trail)
    return ''.join(out)


# --------------------------------------------------------------------------
# Document parsing
# --------------------------------------------------------------------------

class Converter:
    def __init__(self, path: str, mode: str):
        self.zip = zipfile.ZipFile(path)
        self.mode = mode
        self.root = ET.fromstring(self.zip.read('word/document.xml'))
        self.rels = self._load_rels('word/_rels/document.xml.rels')
        self.footnotes = self._load_footnotes()
        self.images: dict[str, str] = {}

    def _load_rels(self, part: str) -> dict[str, tuple[str, str]]:
        rels = {}
        try:
            tree = ET.fromstring(self.zip.read(part))
        except KeyError:
            return rels
        for rel in tree:
            rels[rel.get('Id')] = (rel.get('Type', '').rsplit('/', 1)[-1],
                                   rel.get('Target', ''))
        return rels

    def _load_footnotes(self) -> dict[str, str]:
        notes = {}
        try:
            tree = ET.fromstring(self.zip.read('word/footnotes.xml'))
        except KeyError:
            return notes
        for fn in tree.findall(W + 'footnote'):
            fid = fn.get(W + 'id')
            if fn.get(W + 'type') in ('separator', 'continuationSeparator'):
                continue
            runs: list[Run] = []
            for p in fn.iter(W + 'p'):
                self._walk(p, runs)
            text = render_runs(runs).strip()
            if text:
                notes[fid] = text
        return notes

    # -- run-level walk ----------------------------------------------------

    def _run_format(self, run) -> tuple[bool, bool, bool]:
        rPr = run.find(W + 'rPr')
        code = bold = italic = False
        if rPr is not None:
            style = rPr.find(W + 'rStyle')
            if style is not None and style.get(W + 'val') == 'codeChar':
                code = True
            fonts = rPr.find(W + 'rFonts')
            if fonts is not None and 'Courier' in (fonts.get(W + 'ascii') or ''):
                code = True
            b = rPr.find(W + 'b')
            if b is not None and b.get(W + 'val') not in ('0', 'false'):
                bold = True
            i = rPr.find(W + 'i')
            if i is not None and i.get(W + 'val') not in ('0', 'false'):
                italic = True
        return code, bold, italic

    def _walk(self, elem, out: list[Run], in_ins=False, in_del=False, link=None):
        """Collect runs, resolving tracked changes according to self.mode."""
        for child in elem:
            tag = child.tag
            if tag == W + 'ins':
                self._walk(child, out, True, in_del, link)
            elif tag == W + 'del':
                self._walk(child, out, in_ins, True, link)
            elif tag == W + 'hyperlink':
                rid = child.get(R + 'id')
                target = self.rels.get(rid, (None, None))[1] if rid else None
                self._walk(child, out, in_ins, in_del, target or link)
            elif tag == W + 'r':
                # accept: drop deletions, keep insertions.
                # reject: keep deletions (restoring v1.0), drop insertions.
                if in_del and self.mode == 'accept':
                    continue
                if in_ins and self.mode == 'reject':
                    continue
                self._emit_run(child, out, link, in_del)
            elif tag in (W + 'pPr', W + 'rPr', W + 'sectPr'):
                continue
            else:
                self._walk(child, out, in_ins, in_del, link)

    def _emit_run(self, run, out: list[Run], link, in_del=False):
        code, bold, italic = self._run_format(run)
        for node in run:
            tag = node.tag
            if tag == W + 't':
                if in_del and self.mode == 'accept':
                    continue
                out.append(Run(node.text or '', code, bold, italic, link))
            elif tag == W + 'delText':
                if self.mode == 'reject':
                    out.append(Run(node.text or '', code, bold, italic, link))
            elif tag == W + 'tab':
                out.append(Run(' ', code, bold, italic, link))
            elif tag == W + 'br':
                out.append(Run('\n', code, bold, italic, link))
            elif tag == W + 'footnoteReference':
                fid = node.get(W + 'id')
                if fid in self.footnotes:
                    out.append(Run(footnote=self.footnotes[fid]))
            elif tag in (W + 'drawing', W + 'pict', W + 'object'):
                name = self._extract_image(node)
                if name:
                    out.append(Run(image=name))

    def _extract_image(self, node) -> str | None:
        rid = None
        for blip in node.iter(A + 'blip'):
            rid = blip.get(R + 'embed')
            break
        if rid is None:
            for el in node.iter():
                for key in (R + 'embed', R + 'id'):
                    if el.get(key):
                        rid = el.get(key)
                        break
                if rid:
                    break
        if not rid or rid not in self.rels:
            return None
        target = self.rels[rid][1]
        name = os.path.basename(target)
        if name in FIGURE_OVERRIDES:
            return FIGURE_OVERRIDES[name]
        name = IMAGE_NAMES.get(name, name)
        self.images[name] = 'word/' + target.lstrip('/')
        return name

    # -- block-level -------------------------------------------------------

    @staticmethod
    def _style(p) -> str:
        pPr = p.find(W + 'pPr')
        if pPr is None:
            return ''
        st = pPr.find(W + 'pStyle')
        return st.get(W + 'val') if st is not None else ''

    @staticmethod
    def _list_level(p):
        pPr = p.find(W + 'pPr')
        if pPr is None:
            return None
        numPr = pPr.find(W + 'numPr')
        if numPr is None:
            return None
        ilvl = numPr.find(W + 'ilvl')
        return int(ilvl.get(W + 'val')) if ilvl is not None else 0

    def paragraph(self, p) -> dict:
        runs: list[Run] = []
        self._walk(p, runs)
        return {
            'kind': 'p',
            'style': self._style(p),
            'level': self._list_level(p),
            'text': render_runs(runs).strip(),
            'raw': ''.join(r.text for r in runs if not r.image).strip(),
            'images': [r.image for r in runs if r.image],
        }

    def table(self, tbl) -> dict:
        rows = []
        for tr in tbl.findall(W + 'tr'):
            cells = []
            for tc in tr.findall(W + 'tc'):
                parts = []
                for p in tc.findall(W + 'p'):
                    block = self.paragraph(p)
                    if block['text']:
                        parts.append(block['text'])
                cells.append(' +\n'.join(parts))
            rows.append(cells)
        return {'kind': 'table', 'rows': rows}

    def blocks(self) -> list[dict]:
        body = self.root.find(W + 'body')
        out = []
        for child in body:
            if child.tag == W + 'p':
                out.append(self.paragraph(child))
            elif child.tag == W + 'tbl':
                out.append(self.table(child))
        return out

    def save_images(self, outdir: str):
        target = os.path.join(outdir, 'images')
        os.makedirs(target, exist_ok=True)
        for name, member in self.images.items():
            try:
                with self.zip.open(member) as src, \
                        open(os.path.join(target, name), 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            except KeyError:
                pass


# --------------------------------------------------------------------------
# Section numbering and anchors
# --------------------------------------------------------------------------

def assign_sections(blocks: list[dict]) -> dict[str, str]:
    """Number every heading the way Word did, and give each a stable anchor.

    Returns {section number -> anchor}. Word generated these numbers from a
    list definition, so they exist nowhere in the text; recomputing them is
    what lets us rewrite the hard-typed "Section 6.4.2" references into real
    cross-references.
    """
    counters = [0] * 6
    numbers: dict[str, str] = {}
    used: set[str] = set()
    appendix = 0

    for block in blocks:
        style = block.get('style', '')
        if block['kind'] != 'p' or not block['raw']:
            continue

        if style == 'AppendixHeading1':
            appendix += 1
            number = chr(ord('A') + appendix - 1)
            level = 1
            counters = [0] * 6
        elif style in HEADING_STYLES:
            level = HEADING_STYLES[style]
            counters[level - 1] += 1
            for k in range(level, 6):
                counters[k] = 0
            number = '.'.join(str(c) for c in counters[:level])
        else:
            continue

        anchor = 'sec-' + slugify(block['raw'])
        if anchor in used:  # duplicate titles exist (e.g. Base Macro Conformance)
            anchor = 'sec-%s-%s' % (slugify(block['raw']), number.replace('.', '-'))
        used.add(anchor)

        block['heading_level'] = level
        block['number'] = number
        block['anchor'] = anchor
        numbers[number] = anchor

    return numbers


XREF_RE = re.compile(r'\bSections?\s+(\d+(?:\.\d+)*)'
                     r'((?:\s*(?:,|and|&|through|-|–)\s*\d+(?:\.\d+)*)*)',
                     re.IGNORECASE)


# Code spans and passthroughs are verbatim: a cross-reference rewritten inside
# one renders as the literal text "<<sec-...>>" instead of resolving.
VERBATIM_SPAN = re.compile(
    r'``\+.*?\+``|`\++.*?\++`|pass:c\[(?:\\\]|[^\]])*\]', re.S)


def rewrite_xrefs(text: str, numbers: dict[str, str], report: list) -> str:
    """Turn literal "Section 6.4.2" into a real AsciiDoc cross-reference.

    With :sectnums: and :xrefstyle: short, <<sec-lamps-and-gauges>> renders as
    "Section 6.4.2" and asciidoctor fails the build if the target disappears --
    which is precisely the failure that went unnoticed between 1.0 and 1.1.
    """
    def replace(match: re.Match) -> str:
        head, tail = match.group(1), match.group(2) or ''
        pieces = [head] + re.findall(r'\d+(?:\.\d+)*', tail)
        if not all(p in numbers for p in pieces):
            for p in pieces:
                if p not in numbers:
                    report.append(p)
            return match.group(0)
        if len(pieces) == 1:
            return '<<%s>>' % numbers[head]
        # Keep connective words ("and", ",") between multiple references.
        rendered = '<<%s>>' % numbers[pieces[0]]
        rest = tail
        for p in pieces[1:]:
            rest = rest.replace(p, '<<%s>>' % numbers[p], 1)
        return rendered + rest

    pieces = []
    cursor = 0
    for span in VERBATIM_SPAN.finditer(text):
        pieces.append(XREF_RE.sub(replace, text[cursor:span.start()]))
        pieces.append(span.group(0))          # left exactly as written
        cursor = span.end()
    pieces.append(XREF_RE.sub(replace, text[cursor:]))
    return ''.join(pieces)


# --------------------------------------------------------------------------
# AsciiDoc emission
# --------------------------------------------------------------------------

def emit_blocks(blocks: list[dict], numbers: dict[str, str],
                report: list) -> list[str]:
    """Render a run of blocks, merging consecutive code paragraphs."""
    lines: list[str] = []
    code_buffer: list[str] = []

    def flush_code():
        if not code_buffer:
            return
        while code_buffer and not code_buffer[-1].strip():
            code_buffer.pop()
        while code_buffer and not code_buffer[0].strip():
            code_buffer.pop(0)
        # Word has code-styled paragraphs that hold nothing -- spacers, and in
        # the redline the remains of samples whose every run was deleted.
        # Emitting those would render as an empty code box.
        if not code_buffer:
            return
        lines.append('[source,xml]')
        lines.append('----')
        lines.extend(code_buffer)
        lines.append('----')
        lines.append('')
        code_buffer.clear()

    for block in blocks:
        if block['kind'] == 'table':
            flush_code()
            rows = block['rows']
            if not rows:
                continue
            cols = max(len(r) for r in rows)
            lines.append('[cols="%d*",options="header"]' % cols)
            lines.append('|===')
            for row in rows:
                for cell in row:
                    lines.append('|%s' % rewrite_xrefs(cell, numbers, report))
                lines.append('')
            lines.append('|===')
            lines.append('')
            continue

        style = block.get('style', '')
        text = block['text']

        if style.startswith(DROP_STYLES):
            continue

        if is_code_style(style):
            if style == 'codefirst':
                flush_code()
            code_buffer.append(block['raw'])
            continue

        flush_code()

        if not text:
            continue

        if 'heading_level' in block:
            level = block['heading_level']
            lines.append('[[%s]]' % block['anchor'])
            lines.append('%s %s' % ('=' * (level + 1), text))
            lines.append('')
            continue

        text = rewrite_xrefs(text, numbers, report)
        level = block.get('level')

        # Word's <w:br/> arrives as a newline. Segments that are nothing but an
        # image become block images so they render as figures; the rest are
        # rejoined with hard line breaks. Inside a list item each following
        # segment needs a '+' continuation to stay attached to the item.
        segments = [s.strip() for s in text.split('\n')]
        segments = [s for s in segments if s]
        if not segments:
            continue

        emitted = 0
        pending: list[str] = []

        def flush_text():
            nonlocal emitted
            if not pending:
                return
            body = ' +\n'.join(protect_line_start(p) for p in pending)
            if level is not None:
                if emitted == 0:
                    lines.append('%s %s' % ('*' * (level + 1), body))
                else:
                    lines.append('+')
                    lines.append(body)
            else:
                lines.append(body)
            lines.append('')
            emitted += 1
            pending.clear()

        for segment in segments:
            if re.fullmatch(r'image:[^\s\[]+\[\]', segment):
                flush_text()
                if level is not None and emitted > 0:
                    lines.append('+')
                lines.append('image::' + segment[len('image:'):])
                lines.append('')
                emitted += 1
            else:
                pending.append(segment)
        flush_text()

    flush_code()
    return lines


def chapter_filename(index: int, title: str) -> str:
    return '%02d-%s.adoc' % (index, slugify(title)[:40])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source')
    ap.add_argument('outdir')
    ap.add_argument('--mode', choices=('accept', 'reject'), required=True,
                    help='accept = v1.1 (apply tracked changes); '
                         'reject = v1.0 (discard them)')
    ap.add_argument('--version', required=True)
    ap.add_argument('--revdate', default='')
    args = ap.parse_args()

    conv = Converter(args.source, args.mode)
    blocks = conv.blocks()
    numbers = assign_sections(blocks)

    # Split at each level-1 heading (chapters and appendices alike).
    front: list[dict] = []
    chapters: list[tuple[str, list[dict]]] = []
    current: list[dict] | None = None
    for block in blocks:
        if block.get('heading_level') == 1:
            current = [block]
            chapters.append((block['raw'], current))
        elif current is None:
            front.append(block)
        else:
            current.append(block)

    spec_dir = os.path.join(args.outdir, 'spec')
    os.makedirs(spec_dir, exist_ok=True)
    conv.save_images(spec_dir)

    report: list[str] = []
    written = []

    # Front matter keeps the standing title-page material but drops the
    # generated table of contents.
    front_lines = emit_blocks(front, numbers, report)
    front_name = '00-front-matter.adoc'
    with open(os.path.join(spec_dir, front_name), 'w', encoding='utf-8',
              newline='\n') as fh:
        fh.write('\n'.join(front_lines).rstrip() + '\n')
    written.append(front_name)

    for index, (title, blocks_in) in enumerate(chapters, start=1):
        name = chapter_filename(index, title)
        lines = emit_blocks(blocks_in, numbers, report)
        with open(os.path.join(spec_dir, name), 'w', encoding='utf-8',
                  newline='\n') as fh:
            fh.write('\n'.join(lines).rstrip() + '\n')
        written.append(name)

    master = [
        '= XML Interchange Language for System Dynamics (XMILE) Version %s'
        % args.version,
        ':revnumber: %s' % args.version,
    ]
    if args.revdate:
        master.append(':revdate: %s' % args.revdate)
    master += [
        ':doctype: book',
        ':toc: left',
        ':toclevels: 3',
        ':sectnums:',
        ':sectnumlevels: 4',
        ':sectanchors:',
        ':xrefstyle: short',
        # The specification's own prose says "Section 2", never "Chapter 2";
        # book doctype would otherwise label top-level references as chapters.
        ':chapter-refsig: Section',
        ':section-refsig: Section',
        ':icons: font',
        ':source-highlighter: highlight.js',
        ':imagesdir: images',
        '',
        '// Generated from the Word source by tools/docx2adoc.py.',
        '// Subsequent edits are made here, in AsciiDoc, not in Word.',
        '',
    ]
    for name in written:
        master.append('include::%s[]' % name)
        master.append('')

    with open(os.path.join(spec_dir, 'xmile.adoc'), 'w', encoding='utf-8',
              newline='\n') as fh:
        fh.write('\n'.join(master).rstrip() + '\n')

    print('mode=%s version=%s' % (args.mode, args.version))
    print('  %d chapters, %d headings, %d images'
          % (len(chapters), len(numbers), len(conv.images)))
    if report:
        unique = sorted(set(report), key=lambda s: [int(x) for x in s.split('.')])
        print('  UNRESOLVED cross-references (%d refs, %d distinct): %s'
              % (len(report), len(unique), ', '.join(unique)))
    else:
        print('  all cross-references resolved')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
