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
| `site/` | Website content: the release manifest and the release notes. |
| `tools/` | The one-time Word conversion, plus the build, lint, validate, verify, redline and site used day to day. |
| `archive/` | The original Word documents, kept for provenance. Not edited. |

## Building

```sh
npm install
npm run check          # lint, build, compile the schema, verify signatures
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

## Verifying signatures

`tools/verify_signatures.py` checks the ed25519 signatures in the
`<ai_information>` block, described in Chapter 2 under Signing.

```sh
npm run verify                        # verify spec/schema/*.xmile
npm run verify -- FILE [FILE...]      # verify specific files
npm run verify -- --offline           # build messages, skip the signatures
npm run verify -- --show-message F    # print the message that was built
```

It is written from the specification rather than from any producer's code, so
when it disagrees with a vendor's output, one of the specification, the
producer, or this tool is wrong — and it becomes possible to work out which.

Two signatures are checked. The **main** signature covers the `<status>`
attributes, the variable equations, the per-variable AI states and the log. The
**agentic** signature covers the collated `content` of every `<message>` in
`<agentic_log>`, and is carried in the `{note:signature}` prefix at the head of
`<log>`.

The `<testing>` tag is what makes this useful rather than merely binary. Its
`signed_message_body` holds the exact string the producer signed, so the message
built here can be compared against it directly. That separates *the message was
assembled differently* from *the signature does not match*, which are otherwise
indistinguishable — one wrong character fails exactly like a tampered model. A
file without a `<testing>` block can only report the combined result, which is a
good reason to include one in any file used as a test case.

Verification needs the same Python environment as validation, plus the
`cryptography` package; both are in `tools/requirements.txt`. It also fetches the
public key named by each file's `keyurl`, so it needs network access unless you
pass `--key` with a local copy or `--offline` to skip the signature checks.

`npm run check` runs this too, so CI verifies every sample on each push. All
four currently reproduce their `signed_message_body` exactly and both their
signatures verify, which makes the check meaningful: a change that breaks the
message construction, or a sample re-saved with a stale signature, fails the
build rather than being noticed later.

## Redline for review

`tools/redline.py` produces a reviewable redline of the specification prose,
for circulation to the Technical Committee.

```sh
npm run redline                                # HEAD against origin/main
npm run redline -- --base v1.1                 # against a tag
npm run redline -- --out build/tc-review.html  # choose the output file
```

The result is one self-contained HTML file: no scripts, no external assets, so
it can be attached to an email or opened from a file share. It prints and reads
in light or dark mode.

The 1.0 and 1.1 revisions were reviewed as Word *Compare Documents* output. A
git diff is not a substitute: it shows AsciiDoc source rather than prose, splits
a paragraph across hunks, and cannot say which numbered section a change lands
in. So both revisions are rendered through Asciidoctor first and the *rendered*
text is compared. Cross-references therefore appear as the section numbers a
reviewer will cite, and markup never reaches the page.

Changes are grouped under the section they belong to, with word-level marking
inside a reworded block. Unchanged material is omitted, so the reviewer reads
only what moved.

One thing is deliberately held back. Inserting a single section makes
Asciidoctor renumber every heading, footnote and cross-reference after it, which
in the 1.1 to 1.2 comparison is around sixty blocks that nobody edited. Those
are separated into an "Automatic renumbering" section at the end rather than
dropped, because they are the evidence that every reference followed its anchor
— exactly the failure recorded in `ERRATA-v1.1.adoc`, where renumbering silently
changed what 13 references pointed at.

## Website

`tools/site.mjs` builds the specification website, published at
https://xmile.systemdynamics.org.

```sh
npm run site                       # build to build/site
npm run site -- --skip-assets      # pages only, for a quick look at the layout
npm run site -- --out /tmp/site    # somewhere else
```

The output is a plain static tree with no build step of its own and no external
requests, so it can be served from anywhere:

```
index.html      redirects to whichever release is current
1.0/index.html         release page, and its specification file
1.1/index.html         the current standard
1.1-errata/index.html  under review: 1.1 with the errata corrected
1.2/index.html         under review: the AI information extension
```

Reviews form a chain. Each `under-review` release is measured against the
`reviewBase` named in the manifest, and the page names and links that base, so a
reader can see that the errata corrections are measured against the approved
1.1, and 1.2 against those corrections rather than against 1.1 itself.

Every internal link is relative, so the tree works under any prefix. Each page
also carries a `rel="canonical"` built from the `canonical` field in
`site/releases.json`, which is the only place the domain appears.

The GitHub release page is a fine place to read one release and a poor place to
move between them: no prior or next link, no way to see a draft beside the
standard it will replace, and a URL nobody wants to cite in a committee paper.
This publishes the same content at stable paths, with every version reachable in
one click from any other, and a draft marked as a draft rather than looking like
the standard.

### Content

| Path | What it holds |
| --- | --- |
| `site/releases.json` | The releases, in order: version, designation, date, status, and the git ref each is built from. |
| `site/notes/<version>.adoc` | The release notes for that version. |

`status` drives the page: `current` gets the plain treatment, `superseded` gets a
notice pointing at the current release, and `under-review` gets a draft warning
and a redline link. Exactly one release should be `current`; that is what
`index.html` redirects to.

Adding a release means adding an entry and a notes file. Nothing else changes.

### Assets

Specification files are rendered from each release's **git ref**, not copied from
somewhere, so what the site serves is whatever that tag actually says. A release
under review is built from `HEAD` and gets a redline against the ref named by
`reviewBase`, which is how a reviewer sees what changed without reading the whole
document again.

The XML Schema and the sample models are published the same way, discovered
rather than listed, so adding a sample to the repository publishes it without
anyone registering it. A release whose ref predates the schema simply has none,
which is the case for the 1.0 and 1.1 tags.

The samples are then validated against the schema published beside them, and the
page reports the result. That matters because the answer is not always yes: on
`main` the samples already carry `<ai_information>` and `ai_state` while `main`'s
schema predates both, so the page says so rather than claiming a conformance
nobody checked.

That means a full build checks out each tag in turn, so it is slower than the
other tools. Use `--skip-assets` when iterating on the layout.
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
