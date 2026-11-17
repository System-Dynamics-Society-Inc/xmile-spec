"""Check that every XML example in the specification conforms to the schema.

The specification shows XMILE in two forms: [source,xml] blocks, and runs of
lines that are each a single `+...+` code span (Chapter 6 writes its element
examples that way). Most examples are fragments, a <stock> or an <options>
block, which the schema cannot validate on their own. So each fragment is
placed where it belongs inside a minimal XMILE document, and that document is
validated.

This exists because the specification and its schema drifted apart without
anyone noticing: four style attributes had two spellings, and every Chapter 6
example used the one the schema rejects (see ERRATA-v1.2.adoc). Nothing caught
it because nothing ever ran an example through the schema.

What counts as a finding:

* An example that is not well-formed XML, including one written with
  typographic quotation marks, which no XML parser accepts.
* Any schema error, except that an example containing a line of "..." is
  allowed to be incomplete. It is an outline: a required element left out, or
  a shown element arriving before the omitted one it should follow, is what an
  outline is.

Known disagreements between the text and the schema, which need a decision
rather than an edit, are listed in tools/known-example-exceptions.tsv so that
the check still fails on any new one.

Usage:
    python tools/check_examples.py [--spec spec] [--schema FILE] [--verbose]

Exit status is 0 when every finding is a known exception, 1 otherwise.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

import xmlschema

NS = "http://docs.oasis-open.org/xmile/ns/XMILE/v1.0"
# Prefixes the examples use for vendor extensions. Declared on every wrapper
# so that a fragment like <eqn vendor:origin="..."> is well-formed.
VENDOR_NS = 'xmlns:isee="http://iseesystems.com/XMILE" xmlns:vendor="http://example.com/vendor"'
TYPOGRAPHIC = "“”‘’"
ELLIPSIS = {"...", "…"}

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN = os.path.join(HERE, "known-example-exceptions.tsv")


# ---------------------------------------------------------------- extraction

class Example:
    def __init__(self, path: str, line: int, kind: str, text: str):
        self.path, self.line, self.kind, self.text = path, line, kind, text
        self.file = os.path.basename(path)

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


CODE_SPAN_LINE = re.compile(r"^`\+(.*)\+`\s*$")


def extract(path: str):
    lines = open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("[source,xml]"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("----"):
                j += 1
            k = j + 1
            while k < len(lines) and not lines[k].startswith("----"):
                k += 1
            yield Example(path, i + 1, "block", "\n".join(lines[j + 1:k]))
            i = k + 1
            continue
        # A run of lines that are each nothing but a code span starting with a
        # tag. Blank lines between them belong to the run; prose ends it.
        if CODE_SPAN_LINE.match(lines[i]) and lines[i].startswith("`+<"):
            start, parts = i, []
            while i < len(lines):
                m = CODE_SPAN_LINE.match(lines[i])
                if m:
                    parts.append(m.group(1))
                elif lines[i].strip():
                    break
                i += 1
            yield Example(path, start + 1, "inline", "\n".join(parts))
            continue
        i += 1


# ---------------------------------------------------------------- placement

HEAD = '<header><vendor>v</vendor><product version="1">p</product></header>'
SIM = "<sim_specs><start>0</start><stop>10</stop></sim_specs>"
VIEW_OBJECTS = {"stock", "flow", "aux", "module", "group", "connector", "alias",
                "slider", "numeric_display", "graph", "table", "text_box",
                "stacked_container", "style"}


def doc(body: str) -> str:
    return f'<xmile version="1.2" xmlns="{NS}" {VENDOR_NS}>{body}</xmile>'


def model(variables: str = "", view: str = "") -> str:
    views = f"<views><view>{view}</view></views>" if view else ""
    return f"<model><variables>{variables}</variables>{views}</model>"


def place(first: ET.Element, inner: str, kind: str) -> str | None:
    """A whole document holding the fragment, or None if it has no home."""
    t = first.tag
    if kind == "inline" and t in VIEW_OBJECTS:
        return doc(HEAD + SIM + model(view=inner))
    if t == "header":
        return doc(inner + SIM + model())
    if t == "options":
        return doc(f"<header>{inner}</header>" + SIM + model())
    if t.startswith(("uses_", "has_")):
        return doc(f"<header><options>{inner}</options></header>" + SIM + model())
    if t == "include":
        return doc(f"<header><includes>{inner}</includes></header>" + SIM + model())
    if t == "sim_specs":
        return doc(HEAD + inner + model())
    if t in ("model_units", "behavior", "data", "macro"):
        return doc(HEAD + SIM + inner + model())
    if t == "dimensions" and any(d.get("size") or len(d) for d in first):
        return doc(HEAD + SIM + inner + model())  # file-level dimension definitions
    if t == "model":
        return doc(HEAD + SIM + inner)
    if t == "style":
        return doc(HEAD + SIM + f"<model><variables/><views>{inner}<view/></views></model>")
    if t in ("status", "log", "agent_log", "settings", "testing"):
        status = "" if t == "status" else '<status verified="false"/>'
        return doc(HEAD + f"<ai_information>{status}{inner}</ai_information>" + SIM + model())
    if t in ("stock", "flow", "aux", "gf", "group", "module"):
        return doc(HEAD + SIM + model(variables=inner))
    if t in ("image", "video", "sound", "link"):
        event = f'<event_poster><threshold value="1"><event>{inner}</event></threshold></event_poster>'
        return doc(HEAD + SIM + model(variables=f'<aux name="host">{event}</aux>'))
    if t in ("eqn", "inflow", "outflow", "non_negative", "event_poster", "dimensions",
             "units", "doc", "range", "scale", "format"):
        host = "stock" if t in ("inflow", "outflow") else "flow" if t == "non_negative" else "aux"
        return doc(HEAD + SIM + model(variables=f'<{host} name="host">{inner}</{host}>'))
    return None


# ---------------------------------------------------------------- checking

def local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def prepare(text: str):
    """Strip outline lines and the XML declaration; report what was elided."""
    kept, elided = [], False
    for line in text.split("\n"):
        if line.strip() in ELLIPSIS:
            elided = True
        elif line.strip():
            kept.append(line)
    body = re.sub(r"<\?xml[^?]*\?>", "", "\n".join(kept))
    return body, elided


def close_lone_start_tag(text: str) -> str:
    """<flow name="x" ai_state="C"> on its own shows attributes; close it."""
    m = re.fullmatch(r"\s*<([A-Za-z_][\w:.-]*)\b[^<>]*(?<!/)>\s*(<!--.*?-->)?\s*", text, re.S)
    return f"{text}</{m.group(1)}>" if m else text


def documents(example: Example):
    """Yield (label, xml text or None, findings) for each document to validate."""
    findings = []
    if any(c in example.text for c in TYPOGRAPHIC):
        findings.append("uses typographic quotation marks, which XML does not accept")
    text, elided = prepare(example.text)
    if not text:
        return
    text = close_lone_start_tag(text)

    # A block may show several separate files, each its own <xmile> document.
    whole = re.findall(r"<xmile\b.*?</xmile>", text, re.S)
    if whole and re.sub(r"<xmile\b.*?</xmile>", "", text, flags=re.S).strip() == "":
        for n, d in enumerate(whole, 1):
            label = f"document {n}" if len(whole) > 1 else ""
            yield label, d, elided, list(findings)
            findings = []
        return

    try:
        holder = ET.fromstring(f"<holder xmlns=\"{NS}\" {VENDOR_NS}>{text}</holder>")
    except ET.ParseError as e:
        yield "", None, elided, findings + [f"not well-formed XML: {e}"]
        return
    children = list(holder)
    if not children:
        return
    first = children[0]
    first.tag = local(first.tag)
    body = place(first, text, example.kind)
    if body is None:
        yield "", None, elided, findings + [f"no rule places a <{first.tag}> fragment; extend place()"]
        return
    yield "", body, elided, findings


def validate(schema, body: str, elided: bool):
    errors = set()
    try:
        for err in schema.iter_errors(body):
            reason = str(err.reason or err.message)
            # "..." stands for content left out. The schema reports that as
            # an incomplete element, or, when what was left out precedes a
            # sibling that is shown, as that sibling arriving too early.
            if elided and ("is not complete" in reason
                           or ("Unexpected child" in reason and "expected" in reason)):
                continue
            errors.add(f"{err.path or ''}: {reason}".replace("{" + NS + "}", ""))
    except ET.ParseError as e:
        errors.add(f"not well-formed XML: {e}")
    return sorted(errors)


def load_known():
    known = []
    if os.path.exists(KNOWN):
        for line in open(KNOWN, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            file, first_line, fragment, reason = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
            known.append((file, first_line, fragment, reason))
    return known


def is_known(known, example: Example, body: str, finding: str):
    for file, first_line, fragment, _ in known:
        if file == example.file and first_line in (body or example.text) and fragment in finding:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--spec", default="spec")
    ap.add_argument("--schema", default=os.path.join("spec", "schema", "xmile.xsd.xml"))
    ap.add_argument("--verbose", action="store_true", help="list every example checked")
    args = ap.parse_args()

    schema = xmlschema.XMLSchema11(args.schema)
    known = load_known()
    checked = clean = 0
    excused, failures = [], []

    for path in sorted(glob.glob(os.path.join(args.spec, "[0-9][0-9]-*.adoc"))):
        for example in extract(path):
            for label, body, elided, findings in documents(example):
                checked += 1
                if body is not None:
                    findings += validate(schema, body, elided)
                where = f"{example.where}{' ' + label if label else ''}"
                if not findings:
                    clean += 1
                    if args.verbose:
                        print(f"  ok  {where}")
                    continue
                for f in findings:
                    (excused if is_known(known, example, body, f) else failures).append((where, f))

    for where, f in excused:
        if args.verbose:
            print(f"  known  {where}: {f}")
    for where, f in failures:
        print(f"  {where}: {f}")

    summary = f"{checked} example(s), {clean} conform"
    if excused:
        summary += f", {len(excused)} known exception(s)"
    if failures:
        print(f"examples FAILED: {summary}, {len(failures)} finding(s) above.")
        return 1
    print(f"examples OK: {summary}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
