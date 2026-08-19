# XMILE Specification

The XML Interchange Language for System Dynamics (XMILE) specification, kept as
AsciiDoc source under version control.

The specification was previously maintained as a Word document. This repository
holds the same text as editable source, so changes can be proposed, reviewed and
released the way code is.

## Layout

| Path | Contents |
| --- | --- |
| `spec/` | The specification. `xmile.adoc` is the master file; one file per chapter. |
| `spec/images/` | Figures. |
| `spec/schema/` | `xmile.xsd.xml`, the XML Schema for XMILE documents. XSD 1.1. |
| `tools/` | The one-time Word conversion, plus the build, lint and validate used day to day. |
| `archive/` | The original Word documents, kept for provenance. Not edited. |

## Building

```sh
npm install
npm run check          # lint, build, and compile the schema
npm run build:release  # single self-contained file, figures embedded
```

Node is all you need to lint and build. `check` runs in CI on every push and
pull request, and a warning fails the build.

`build` emits HTML alongside an `images/` directory, which is what you want
while editing. `build:release` embeds the figures in the page instead, so the
result is one file that survives being downloaded on its own — that is what is
attached to a GitHub release.

## Validating

`tools/validate.py` checks XMILE documents against `spec/schema/xmile.xsd.xml`.

```sh
npm run validate                     # compile the schema and stop
npm run validate -- model.stmx       # also validate documents
npm run validate -- "models/*.stmx"  # globs are expanded by the tool
```

With no documents it only compiles the schema, which is what `check` and CI do.
On its own that catches a schema broken by an edit.

This is the one part of the toolchain that is not Node. The schema is an **XSD
1.1** schema and has to be: Chapter 2 promises "provision for vendor specific
additions", and expressing that needs `xs:defaultOpenContent` and schema-level
`defaultAttributes`, neither of which exists in XSD 1.0, while `<header>` and
`<style>` are `xs:all` groups, which in XSD 1.0 cannot contain a wildcard at
all. No Node implementation of XSD 1.1 exists.

```sh
python -m venv .venv
.venv/Scripts/activate            # or: source .venv/bin/activate
python -m pip install -r tools/requirements.txt
```

`npm run validate` looks for an activated virtualenv, then a `.venv` or `venv`
in the repository, then the system Python, and names what is missing if none of
them works.

Note that **xmllint and the .NET `XmlSchemaSet` implement XSD 1.0 only**. They
reject this schema rather than skip it, despite the `vc:minVersion="1.1"` that
asks them to. Xerces-J and Saxon-EE read it correctly.

## History

Version 1.0 is the first commit and is tagged `v1.0`. The changes that produced
version 1.1 follow as one commit per change, so each can be read, reviewed or
reverted on its own:

```
v1.0  Import XMILE 1.0 (OASIS Candidate Standard 01, 29 July 2015)
      Add array sub-range support
      Remove knobs; sliders become the sole slider-style input
      Remove switches and option groups
      Remove numeric inputs and list input devices
      Remove graphical inputs
      Remove lamps and gauges
      Remove graphics frames
      Remove buttons
v1.1  Rebrand to SDS and bump the specification to version 1.1
```

Both versions were generated from a single source: `archive/xmile-v1.1-redline.docx`
is a Word *Compare Documents* result, so rejecting every tracked change yields
1.0 and accepting every change yields 1.1. Running both through the same
converter is what keeps the diff between the two tags free of conversion noise —
it contains only real editorial changes. The 1.0 output was verified against the
independently supplied `archive/xmile-v1.0-cos01.docx`: the text is identical.

## Cross-references

In the Word original, a reference such as "Section 6.4.2" was ordinary typed
text. Nothing connected it to the section it named, so nothing could notice when
that section moved or disappeared — and by version 1.1, 19 references pointed at
sections that no longer existed and 13 more silently pointed at different
content. See `ERRATA-v1.1.adoc`.

Here every reference is a real cross-reference:

```asciidoc
See <<sec-lamps-and-gauges>> for the zone definitions.
```

Asciidoctor renders that as "Section 6.4.2", renumbers it automatically, and
fails the build if the target is gone. `tools/lint.mjs` rejects a reference
typed as literal text, so it cannot silently reintroduce the old problem.

When adding a section, give it an anchor immediately above the title:

```asciidoc
[[sec-sub-ranges]]
===== Sub Ranges
```

## Editing

Edit the AsciiDoc under `spec/`. Do not edit the Word files in `archive/`, and
do not regenerate `spec/` from them — the conversion has already happened, and
the AsciiDoc is now the authority.

`tools/docx2adoc.py` and `tools/build_history.py` are kept only to document how
this repository was produced.
