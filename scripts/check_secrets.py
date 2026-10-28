"""Fail on likely credentials/payment data; never print the matched values.

This is a precautionary pattern scan, not a guarantee that no secret exists.
Use --git-dir PATH to inspect all reachable Git blobs, including old revisions.
Use --stdin to inspect a bounded log without saving it.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

LIMIT = 256 * 1024
PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'GitLab credential': re.compile(r'\bgl(?:pat|rt|dt|ptt)-[A-Za-z0-9_-]{16,}\b'),
    'GitHub credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'API credential': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b'),
    'AWS credential': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'Slack credential': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    'payment field': re.compile(r'''(?i)\b(?:cvv|cvc|card_number|cardnumber)\s*[:=]\s*["']?\d[\d -]{2,}'''),
}
PAN = re.compile(r'(?<![A-Za-z0-9_])(?:[0-9][ -]?){12,18}[0-9](?![A-Za-z0-9_])')
EXCLUDED = {'.git', '.audit', '.validation-deps', '.venv', '__pycache__', 'build', 'DerivedData'}
FORBIDDEN_SUFFIXES = {'.p12', '.p8', '.mobileprovision', '.key', '.pem'}


def luhn(value):
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) < 2:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return total % 10 == 0


def findings(name, data):
    path = Path(name)
    if (path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in
            {'.env', 'Local.xcconfig', 'id_rsa', 'id_ed25519'} or
            (path.name.startswith('.env.') and path.name != '.env.example')):
        yield f'{name}: prohibited credential/configuration file'
    if len(data) > LIMIT:
        yield f'{name}: exceeds safe scan limit; manual inspection required'
        return
    if b'\0' in data:
        yield f'{name}: binary source file requires explicit review'
        return
    text = data.decode('utf-8', errors='replace')
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                yield f'{name}:{number}: possible {label}'
        if any(luhn(match.group()) for match in PAN.finditer(line)):
            yield f'{name}:{number}: possible payment card number'


def git_bytes(git_dir, *args):
    return subprocess.check_output(['git', '--git-dir', str(git_dir), *args])


def sources(args):
    if args.stdin:
        yield 'CI log', sys.stdin.buffer.read(LIMIT + 1)
    elif args.git_dir:
        seen = set()
        for oid in git_bytes(args.git_dir, 'rev-list', '--all').decode().splitlines():
            yield f'commit:{oid[:8]}', git_bytes(args.git_dir, 'cat-file', 'commit', oid)
        objects = git_bytes(args.git_dir, 'rev-list', '--objects', '--all').decode().splitlines()
        for entry in objects:
            oid, _, name = entry.partition(' ')
            if oid in seen or not name:
                continue
            seen.add(oid)
            if git_bytes(args.git_dir, 'cat-file', '-t', oid).strip() != b'blob':
                continue
            size = int(git_bytes(args.git_dir, 'cat-file', '-s', oid))
            if size > LIMIT:
                yield name, b'x' * (LIMIT + 1)
            else:
                yield name, git_bytes(args.git_dir, 'cat-file', 'blob', oid)
    else:
        root = Path(__file__).resolve().parents[1]
        for path in root.rglob('*'):
            relative = path.relative_to(root)
            if any(part in EXCLUDED or part.endswith(('.xcodeproj', '.xcworkspace')) for part in relative.parts):
                continue
            if not path.is_file() or path.name == 'PinduoTranslate-source.zip':
                continue
            if path.name == 'Local.xcconfig' and not (root / '.git').exists():
                # Local-only signing configuration is intentionally excluded from Git.
                continue
            with path.open('rb') as stream:
                yield relative.as_posix(), stream.read(LIMIT + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--git-dir', type=Path)
    group.add_argument('--stdin', action='store_true')
    args = parser.parse_args()
    count = 0
    problems = []
    for name, data in sources(args):
        count += 1
        problems.extend(findings(name, data))
    if problems:
        print('\n'.join(problems[:30]), file=sys.stderr)
        print(f'FAIL: {len(problems)} findings; matched values were not printed.', file=sys.stderr)
        return 1
    print(f'PASS: {count} text objects checked; no matching credentials or payment card numbers.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
