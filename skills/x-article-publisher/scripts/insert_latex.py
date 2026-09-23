#!/usr/bin/env python3
"""Insert display-math formulas into an X Article through Insert > LaTeX.

X Articles renders math ONLY via the editor's Insert > LaTeX dialog; typing
`$$...$$` (or any LaTeX source) into the editor is ignored and stays plain
text. So each formula needs:

    click target block -> End -> Add Media -> LaTeX -> fill -> Insert

We insert by matching the marker text `___LATEX_N___` (placed in the body
during the text paste), NOT by block index, because Draft.js block indices
shift as new KaTeX blocks are inserted.

Usage:
    python insert_latex.py parsed.json [--session x-article] [--start N] [--end N]

Requires: playwright-cli (WorkBuddy) on PATH, or set PLAYWRIGHT_CLI.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CLI = os.environ.get(
    'PLAYWRIGHT_CLI',
    r'C:/Users/liruqi/.workbuddy-ai/binaries/node/versions/22.22.2-2/playwright-cli.cmd',
)

# Must stay on ONE line: playwright-cli.cmd mangles newlines in the argument.
SNIPPET = (
    "async page => { "
    "const MARKER = __MARKER__; const TEX = __TEX__; "
    "const ed = page.locator('[contenteditable=\"true\"]').first(); "
    "const root = ed.locator('> *').first(); "
    "const before = await root.evaluate(e => e.children.length); "
    "const bTex = await root.evaluate(e => e.querySelectorAll('.katex').length); "
    "let foundIdx = -1; "
    "const cnt = await root.evaluate(e => e.children.length); "
    "for (let i = 0; i < cnt; i++) { "
    "  const t = await root.locator('> *').nth(i).evaluate(e => e.innerText || e.textContent || ''); "
    "  if (t.trim() === MARKER) { foundIdx = i; break; } "
    "} "
    "if (foundIdx < 0) return { foundIdx: -1, error: 'marker not found' }; "
    "await root.locator('> *').nth(foundIdx).click(); "
    "await page.keyboard.press('End'); "
    "await page.getByRole('button', { name: 'Add Media' }).click(); "
    "await page.getByRole('menuitem', { name: 'LaTeX' }).click(); "
    "await page.getByRole('textbox', { name: 'Add a LaTeX expression here' }).fill(TEX); "
    "await page.getByRole('dialog').getByRole('button', { name: 'Insert' }).click(); "
    "const deadline = Date.now() + 25000; "
    "let after = before, aTex = bTex; "
    "while (Date.now() < deadline) { "
    "  await page.waitForTimeout(400); "
    "  after = await root.evaluate(e => e.children.length); "
    "  aTex = await root.evaluate(e => e.querySelectorAll('.katex').length); "
    "  if (after > before || aTex > bTex) break; } "
    "return { foundIdx: foundIdx, before: before, after: after, "
    "bTex: bTex, aTex: aTex, grew: (after > before) || (aTex > bTex) }; }"
)


def log(msg, logfile=None):
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    print(line, flush=True)
    if logfile:
        with open(logfile, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


def js_literal(s: str) -> str:
    """JSON is a valid JS string literal for our purposes."""
    return json.dumps(s, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('parsed', help='JSON produced by parse_markdown.py')
    ap.add_argument('--session', default='x-article')
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--end', type=int, default=None)
    ap.add_argument('--log', default='insert_latex_log.txt')
    ap.add_argument('--marker-prefix', default='___LATEX_',
                    help='marker prefix used in pasted plain text')
    args = ap.parse_args()

    data = json.loads(Path(args.parsed).read_text(encoding='utf-8'))
    blocks = data.get('latex_blocks', [])
    if not blocks:
        print('no latex_blocks in JSON — did you run with --math-mode latex?')
        return 1

    # Process highest index first so earlier markers don't shift before we
    # visit them.
    todo = sorted(blocks, key=lambda b: -b.get('index', 0))
    todo = todo[args.start:args.end]
    log(f'=== inserting {len(todo)} LaTeX blocks ===', args.log)

    ok = fail = 0
    # Use an ABSOLUTE path: playwright-cli runs as a daemon whose cwd is not
    # necessarily ours, so a relative --filename would not resolve.
    tmp_dir = Path.cwd() / 'scripts' / '_tmp'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for n, item in enumerate(todo, start=args.start):
        marker = f'{args.marker_prefix}{item["index"]}___'
        code = (SNIPPET.replace('__MARKER__', js_literal(marker))
                       .replace('__TEX__', js_literal(item['latex'])))
        # Write the JS snippet to a temp file to avoid cmd.exe parsing
        # problems when the LaTeX contains chars like `|` (absolute-value
        # bars in `\log|t|`).
        tmp_file = tmp_dir / f'_latex_{n:03d}.js'
        tmp_file.write_text(code, encoding='utf-8')
        r = subprocess.run([DEFAULT_CLI, '-s=' + args.session, 'run-code',
                            '--filename', str(tmp_file)],
                           capture_output=True, text=True, encoding='utf-8')
        out = (r.stdout or '') + (r.stderr or '')
        grew = ('"grew":true' in out.replace(' ', '') or
                '"grew": true' in out)
        tex = item['latex'].replace('\n', ' ')[:45]
        if grew:
            ok += 1
            log(f'[{n}] OK   marker={marker}  {tex}', args.log)
        else:
            fail += 1
            log(f'[{n}] FAIL marker={marker}  {tex}  :: '
                f'{out.strip()[:160]}', args.log)

    log(f'=== done: ok={ok} fail={fail} ===', args.log)
    return 0 if fail == 0 else 2


if __name__ == '__main__':
    sys.exit(main())