"""Build a deterministic source-only ZIP after a fail-closed secret/file scan.
Usage: python scripts/release.py ../artifacts/browser-agent-ru-final-current.zip
No browser profiles, environment files, caches or generated output are copied.
"""
import argparse
import hashlib
import re
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {
    'README.md','ACCEPTANCE.md','LIVE_E2E.md','TEST_REPORT.md','DEMO_SCRIPT.md',
    'RU_SITES.md','TERMUX.md','CHANGELOG.md','main.py','pytest.ini',
    '.env.example','.gitignore','requirements.txt','requirements-core.txt',
    'requirements-webdriver.txt','requirements-test.txt',
}
SOURCE_DIRS = {'browser_agent','tests','scripts','docs'}
SOURCE_SUFFIXES = {'.py','.js','.html','.md','.txt'}
DENIED_PARTS = {'__pycache__','.pytest_cache','.venv','node_modules','.git','profiles',
                'browser-profile','.chrome-profile','.auth','logs','traces','release','dist'}
PATTERNS = {
    'OpenAI-style key': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'GitHub token': re.compile(r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}'),
    'AWS access key': re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential URL': re.compile(r"""https?://[^\s/:"']+:[^\s/@"']+@[^\s"'<>]+"""),
    'JWT token': re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}'),
    'JWE token': re.compile(r'\beyJ[A-Za-z0-9_-]{15,}(?:\.[A-Za-z0-9_-]{1,}){4}'),
}
# Deliberately fake credential URL used to prove rejection by URL validation.
TEST_FIXTURES = {'tests/test_core.py': {'https://' + 'secret:password@' + 'example.org'}}


def files():
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file(): continue
        rel=p.relative_to(ROOT)
        if any(part in DENIED_PARTS for part in rel.parts): continue
        if rel.as_posix() in TOP_FILES or (rel.parts[0] in SOURCE_DIRS and p.suffix in SOURCE_SUFFIXES):
            if p.is_symlink(): raise ValueError(f'Symlink rejected: {rel}')
            lower=p.name.lower()
            if lower != '.env.example' and (lower.startswith('.env') or any(x in lower for x in ('cookie','storage-state','storage_state','password','token','backup')) or lower.endswith(('.bak','~'))):
                raise ValueError(f'Sensitive/temporary filename rejected: {rel}')
            yield p,rel


def scan(selected):
    findings=[]
    for path,rel in selected:
        text=path.read_text(encoding='utf-8')
        for label,pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if match.group() in TEST_FIXTURES.get(rel.as_posix(),set()): continue
                findings.append(f'{rel}: {label}')  # Never print potential secret values.
        if rel.name == '.env.example':
            for line in text.splitlines():
                if re.match(r'^(?:(?:LLM|ZAI)_API_KEY|GIGACHAT_AUTH_KEY)\s*=\s*\S',line):
                    findings.append('.env.example: nonempty API/authorization key')
    if findings: raise ValueError('Release scan failed:\n'+'\n'.join(findings))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    selected=list(files())
    scan(selected)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path,rel in selected:
            info=zipfile.ZipInfo('browser-agent-ru-final/'+rel.as_posix(),date_time=(2026,9,22,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            z.writestr(info,path.read_bytes())
    digest=hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f'SCAN PASS: {len(selected)} source/documentation files; no matched secrets or auth artifacts.')
    print(f'SHA256 {digest}')
    print(args.output.resolve())


if __name__=='__main__':
    main()
