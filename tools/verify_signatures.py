"""Verify the ed25519 signatures in the <ai_information> block of XMILE files.

This is an independent implementation of Chapter 2, Signing. It reconstructs the
signed message from the file, then checks the signature against the public key
named by the keyurl attribute. Its value is that it is written from the
specification rather than from any vendor's code, so a disagreement between this
tool and a producer means the specification, the producer, or this tool is
wrong -- and which of those it is can then be worked out.

Two signatures are checked:

  main      over the status attributes, the variable equations, the per-variable
            AI states and the log, as assembled below.
  agentic   over the collated content of every <message> in <agentic_log>,
            carried in the {note:signature} prefix at the head of <log>.

The <testing> tag is what makes this reliable. Its signed_message_body attribute
holds the exact string the producer signed, so when it is present the message
this tool builds can be compared against it directly. That separates "the
message was assembled differently" from "the signature does not match", which
are otherwise indistinguishable: a single wrong character produces the same
failure as a tampered model. A file without <testing> can only report the
combined result.

Usage:
  python tools/verify_signatures.py                     verify spec/schema/*.xmile
  python tools/verify_signatures.py FILE [FILE...]      verify specific files
  python tools/verify_signatures.py --key KEY.txt       use a local public key
  python tools/verify_signatures.py --offline           skip signature checks
  python tools/verify_signatures.py --show-message      print the built message
"""
import argparse
import base64
import binascii
import glob
import re
import struct
import sys
import urllib.request
import xml.etree.ElementTree as ET

XMILE_NS = 'http://docs.oasis-open.org/xmile/ns/XMILE/v1.0'
X = '{%s}' % XMILE_NS
VARIABLE_TAGS = ('stock', 'flow', 'aux')

# Chapter 2, Signing: "all whitespace (space, tab, line feed, carriage return)
# and all underbar characters removed".
SQUASH = re.compile(r'[\s_]')

# Chapter 4: a name or equation writes non-printable characters as XMILE
# identifier escapes, so a newline inside a variable name is the two characters
# backslash and n rather than a newline. Those escapes have to be resolved to
# the characters they stand for before whitespace is removed, otherwise a name
# such as "excess deaths\nfrom crowding" keeps a stray backslash-n in the signed
# message and the signature fails for a reason nothing in the file reveals.
ESCAPES = {'n': '\n', 't': '\t', '\\': '\\'}


def unescape(text):
    out = []
    index = 0
    text = text or ''
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text) and text[index + 1] in ESCAPES:
            out.append(ESCAPES[text[index + 1]])
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)

# The {note:signature} prefix. The note may be any text without a colon or a
# closing brace, which is what makes this greedy-free match unambiguous.
LOG_PREFIX = re.compile(r'^\{([^:}]*):([A-Za-z0-9+/=]+)\}')


def squash(text):
    return SQUASH.sub('', unescape(text))


class Problem(Exception):
    pass


# --------------------------------------------------------------- public key --

def parse_public_key(text):
    """Accept an OpenSSH ed25519 blob or a bare base64 32-byte key."""
    try:
        blob = base64.b64decode(text.strip().split()[-1], validate=False)
    except (binascii.Error, IndexError) as exc:
        raise Problem('public key is not valid base 64: %s' % exc)

    if len(blob) == 32:
        return blob

    # OpenSSH wire format: uint32 len, "ssh-ed25519", uint32 len, 32-byte key.
    try:
        offset = 0
        length = struct.unpack('>I', blob[offset:offset + 4])[0]
        offset += 4
        algorithm = blob[offset:offset + length].decode('ascii')
        offset += length
        length = struct.unpack('>I', blob[offset:offset + 4])[0]
        offset += 4
        key = blob[offset:offset + length]
    except Exception as exc:                                   # noqa: BLE001
        raise Problem('public key is neither 32 raw bytes nor an OpenSSH blob: %s' % exc)

    if algorithm != 'ssh-ed25519' or len(key) != 32:
        raise Problem('public key is %s with a %d byte key; expected ssh-ed25519 and 32'
                      % (algorithm, len(key)))
    return key


class KeyStore:
    """Fetches public keys by URL, once per run."""

    def __init__(self, local_key=None, offline=False):
        self.offline = offline
        self.cache = {}
        self.pinned = None
        if local_key:
            with open(local_key, encoding='utf-8') as handle:
                self.pinned = parse_public_key(handle.read())

    def get(self, url):
        if self.pinned is not None:
            return self.pinned
        if self.offline:
            raise Problem('offline, and no --key supplied')
        if url not in self.cache:
            if not url or not url.lower().startswith('https://'):
                raise Problem('keyurl is not an https URL: %r' % url)
            try:
                body = urllib.request.urlopen(url, timeout=30).read().decode('utf-8')
            except Exception as exc:                           # noqa: BLE001
                raise Problem('could not fetch %s: %s' % (url, exc))
            self.cache[url] = parse_public_key(body)
        return self.cache[url]


# ------------------------------------------------------------ message build --

def variable_equation(variable):
    """The equation as it enters the message.

    A non-apply-to-all array carries no <eqn> of its own. Its equations sit in
    one <element> per array entry, and Chapter 2 wants them "listed from the
    first to the last element with no separation between them", so three
    elements of 20, 10 and 12 contribute 201012.
    """
    elements = variable.findall(X + 'element')
    if elements:
        parts = []
        for element in elements:
            equation = element.find(X + 'eqn')
            parts.append(squash(equation.text if equation is not None else ''))
        return ''.join(parts)
    equation = variable.find(X + 'eqn')
    return squash(equation.text if equation is not None else '')


def collect_variables(root):
    """Stocks, flows and auxiliaries, in the order Chapter 2 prescribes.

    "variables in the root module appear first, followed by the module variables
    with the variable names module qualified". A <model> with no name attribute
    is the root; every named one is a module, and its variables are qualified
    with that name and a dot. The name is squashed like everything else, so a
    module called Premium_Housing1 qualifies as PremiumHousing1.

    Anything that is not a stock, flow or auxiliary is excluded, which is why a
    root holding nothing but <module> entries contributes no variables at all.
    """
    root_variables = []
    module_variables = []
    for model in root.iter(X + 'model'):
        variables = model.find(X + 'variables')
        if variables is None:
            continue
        name = model.get('name')
        prefix = '%s.' % squash(name) if name else ''
        target = module_variables if name else root_variables
        for child in variables:
            if child.tag[len(X):] not in VARIABLE_TAGS:
                continue
            # The sort key is the name as written, not the squashed form.
            # "low cost hous prog" precedes "low cost housing constr des"
            # because a space precedes "i", while their squashed forms sort the
            # other way round. Producers order on the written name.
            target.append((
                (squash(name).lower() if name else '', child.get('name').lower()),
                prefix + squash(child.get('name')),
                variable_equation(child),
                child.get('ai_state') or '',
            ))

    # Case-insensitive: producers sort "births" before "Rabbit Population",
    # which a plain sort would not, since every capital letter precedes every
    # lower-case one in code point order.
    root_variables.sort(key=lambda item: item[0])
    module_variables.sort(key=lambda item: item[0])
    return [entry[1:] for entry in root_variables + module_variables]

def build_message(root):
    ai = root.find(X + 'ai_information')
    status = ai.find(X + 'status')
    if status is None:
        raise Problem('<ai_information> has no <status>')

    # signature is the output, so it is not part of what is signed.
    tokens = ['%s=%s' % (squash(k), squash(v))
              for k, v in sorted(status.attrib.items(), key=lambda kv: squash(kv[0]))
              if k != 'signature']

    flag = lambda name: status.get(name) == 'true'             # noqa: E731
    if flag('want_var_info') or flag('want_var_status'):
        variables = collect_variables(root)
        if flag('want_var_info'):
            tokens += ['%s=%s' % (name, eqn) for name, eqn, _ in variables]
        if flag('want_var_status'):
            tokens.append(''.join(state for _, _, state in variables))

    message = ' '.join(tokens) + ' '
    if flag('want_log'):
        log = ai.find(X + 'log')
        message += squash(log.text if log is not None else '')

    # "If there is a terminal space ... it MUST be removed."
    return message[:-1] if message.endswith(' ') else message


def agentic_candidates(ai):
    """What the agentic signature might cover, most-specified first.

    Chapter 2 says the message is the collated content of every <message>. An
    earlier draft said it was the collated type, and files in circulation were
    signed that way, so both are tried and the report says which one matched.
    Producers and the specification currently disagree here, and a tool that
    only tried the specified rule would report "signature does not match" for a
    file that is internally consistent under the other one.
    """
    log = ai.find(X + 'agentic_log')
    if log is None:
        return None
    messages = log.findall(X + 'message')
    return [
        ('content', ''.join(m.get('content', '') for m in messages)),
        ('type', ''.join(m.get('type', '') for m in messages)),
    ]


def agentic_signature(ai):
    log = ai.find(X + 'log')
    match = LOG_PREFIX.match((log.text or '') if log is not None else '')
    return match.group(2) if match else None


# ------------------------------------------------------------------ report ---

def verify(signature_b64, message, key):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        raw = base64.b64decode(signature_b64 or '', validate=False)
    except binascii.Error:
        return False, 'signature is not valid base 64'
    if len(raw) != 64:
        return False, 'signature is %d bytes; ed25519 signatures are 64' % len(raw)
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(raw, message.encode('utf-8'))
    except InvalidSignature:
        return False, 'signature does not match'
    return True, None


def first_difference(got, expected):
    for i, (a, b) in enumerate(zip(got, expected)):
        if a != b:
            return i, got[max(0, i - 30):i + 30], expected[max(0, i - 30):i + 30]
    if len(got) != len(expected):
        i = min(len(got), len(expected))
        return i, got[max(0, i - 30):], expected[max(0, i - 30):]
    return None


def check_file(path, keys, show_message):
    """Returns (status, [lines]) where status is ok / failed / skipped."""
    lines = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return 'failed', ['not well-formed XML: %s' % exc]

    ai = root.find(X + 'ai_information')
    if ai is None:
        return 'skipped', ['no <ai_information>']

    status_el = ai.find(X + 'status')
    message = build_message(root)
    if show_message:
        lines.append('message: %r' % message)

    failed = False

    testing = ai.find(X + 'testing')
    if testing is not None:
        expected = testing.get('signed_message_body')
        if expected is None:
            lines.append('testing block has no signed_message_body')
        elif message == expected:
            lines.append('message vs <testing>  matches (%d chars)' % len(message))
        else:
            failed = True
            diff = first_difference(message, expected)
            lines.append('message vs <testing>  DIFFERS (built %d chars, expected %d)'
                         % (len(message), len(expected)))
            if diff:
                index, got, exp = diff
                lines.append('  first difference at character %d' % index)
                lines.append('    built    …%s…' % got)
                lines.append('    expected …%s…' % exp)
    else:
        lines.append('message vs <testing>  no <testing> block to compare against')

    algorithm = status_el.get('algorithm')
    if algorithm and algorithm != 'ed25519':
        return 'failed', lines + ['unsupported algorithm %r' % algorithm]

    try:
        key = keys.get(status_el.get('keyurl'))
    except Problem as exc:
        lines.append('main signature       not checked: %s' % exc)
        return ('failed' if failed else 'skipped'), lines

    good, why = verify(status_el.get('signature'), message, key)
    lines.append('main signature       %s' % ('verified' if good else 'FAILED: %s' % why))
    failed = failed or not good

    candidates = agentic_candidates(ai)
    if candidates is not None:
        sig = agentic_signature(ai)
        if sig is None:
            failed = True
            lines.append('agentic signature    FAILED: <agentic_log> present but <log> '
                         'carries no {note:signature} prefix')
        else:
            matched = None
            for label, body in candidates:
                good, _ = verify(sig, body, key)
                if good:
                    matched = label
                    break
            if matched == 'content':
                lines.append('agentic signature    verified (over collated content)')
            elif matched == 'type':
                lines.append('agentic signature    verified, but over collated TYPE, not '
                             'content as Chapter 2 requires')
                failed = True
            else:
                lines.append('agentic signature    FAILED: matches neither collated '
                             'content nor collated type')
                failed = True

    return ('failed' if failed else 'ok'), lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='verify_signatures.py',
        description='Verify ed25519 signatures in XMILE <ai_information> blocks.')
    parser.add_argument('files', nargs='*',
                        help='XMILE files (default: spec/schema/*.xmile)')
    parser.add_argument('--key', metavar='FILE',
                        help='public key to use instead of fetching each keyurl')
    parser.add_argument('--offline', action='store_true',
                        help='build and compare messages, but do not check signatures')
    parser.add_argument('--show-message', action='store_true',
                        help='print the constructed message for each file')
    args = parser.parse_args(argv)

    try:
        import cryptography                                    # noqa: F401
    except ImportError:
        if not args.offline:
            print('verify FAILED: the cryptography package is not installed.\n'
                  'Install it with:  python -m pip install -r tools/requirements.txt\n'
                  'Or rerun with --offline to check message construction only.',
                  file=sys.stderr)
            return 2

    paths = []
    for pattern in (args.files or ['spec/schema/*.xmile']):
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches and not any(ch in pattern for ch in '*?['):
            print('verify FAILED: file not found: %s' % pattern, file=sys.stderr)
            return 2
        paths.extend(matches)

    if not paths:
        print('no files to verify')
        return 0

    try:
        keys = KeyStore(args.key, args.offline)
    except Problem as exc:
        print('verify FAILED: %s' % exc, file=sys.stderr)
        return 2

    counts = {'ok': 0, 'failed': 0, 'skipped': 0}
    for path in paths:
        try:
            state, lines = check_file(path, keys, args.show_message)
        except Problem as exc:
            state, lines = 'failed', [str(exc)]
        counts[state] += 1
        marker = {'ok': 'OK', 'failed': 'FAILED', 'skipped': '--'}[state]
        print('%s  %s' % (path, marker))
        for line in lines:
            print('    %s' % line)

    print('\n%d verified, %d failed, %d skipped'
          % (counts['ok'], counts['failed'], counts['skipped']))
    return 1 if counts['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
