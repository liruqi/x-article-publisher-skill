#!/usr/bin/env python3
"""
Parse Markdown for X Articles publishing.

Extracts:
- Title (from first H1/H2 or first line)
- Cover image (first image)
- Content images with block index for precise positioning
- Dividers (---) with block index for menu insertion
- HTML content (images and dividers stripped)

Usage:
    python parse_markdown.py <markdown_file> [--output json|html]

Output (JSON):
{
    "title": "Article Title",
    "cover_image": "/path/to/cover.jpg",
    "content_images": [
        {"path": "/path/to/img.jpg", "block_index": 3, "after_text": "context..."},
        ...
    ],
    "dividers": [
        {"block_index": 7, "after_text": "context..."},
        ...
    ],
    "html": "<p>Content...</p><h2>Section</h2>...",
    "total_blocks": 25
}

The block_index indicates which block element (0-indexed) the image/divider should follow.
This allows precise positioning without relying on text matching.

Note: Dividers must be inserted via X Articles' Insert > Divider menu, not HTML <hr> tags.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path


# Common search directories for missing images
SEARCH_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home() / "Pictures",
]


def find_image_file(original_path: str, filename: str) -> tuple[str, bool]:
    """Find an image file, searching common directories if not found at original path.
    
    Args:
        original_path: The resolved absolute path from markdown
        filename: Just the filename to search for
    
    Returns:
        (found_path, exists): The path to use and whether file exists
    """
    if os.path.isfile(original_path):
        return original_path, True
    
    for search_dir in SEARCH_DIRS:
        candidate = search_dir / filename
        if candidate.is_file():
            print(f"[parse_markdown] Image not found at '{original_path}', using '{candidate}' instead", file=sys.stderr)
            return str(candidate), True
    
    print(f"[parse_markdown] WARNING: Image not found: '{original_path}' (also searched {[str(d) for d in SEARCH_DIRS]})", file=sys.stderr)
    return original_path, False


# ---------------------------------------------------------------------------
# Math (LaTeX) handling
#
# X Articles renders math ONLY through the editor's **Insert > LaTeX** dialog.
# Typing `$$...$$` or any LaTeX source straight into the editor is ignored and
# stays plain text (verified 2026-09-23). So:
#   * display math ($$...$$) -> a `latex_blocks[]` entry, to be inserted through
#     that dialog at its block_index position (rendered by KaTeX);
#   * inline math ($...$) -> readable Unicode text, since there is no inline
#     LaTeX affordance in the editor.
# ---------------------------------------------------------------------------

DISPLAY_MATH = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
INLINE_MATH = re.compile(r'(?<!\$)\$(?!\$)([^\n$]+?)(?<!\$)\$(?!\$)')

# Greek letters and symbols -> Unicode
_SYMBOLS = {
    'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε',
    'varepsilon': 'ε', 'zeta': 'ζ', 'eta': 'η', 'theta': 'θ', 'iota': 'ι',
    'kappa': 'κ', 'lambda': 'λ', 'mu': 'μ', 'nu': 'ν', 'xi': 'ξ', 'pi': 'π',
    'rho': 'ρ', 'sigma': 'σ', 'tau': 'τ', 'upsilon': 'υ', 'phi': 'φ',
    'varphi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω',
    'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ', 'Lambda': 'Λ', 'Xi': 'Ξ',
    'Pi': 'Π', 'Sigma': 'Σ', 'Phi': 'Φ', 'Psi': 'Ψ', 'Omega': 'Ω',
    'infty': '∞', 'partial': '∂', 'nabla': '∇', 'forall': '∀', 'exists': '∃',
    'emptyset': '∅', 'in': '∈', 'notin': '∉', 'subset': '⊂', 'cup': '∪',
    'cap': '∩', 'pm': '±', 'mp': '∓', 'times': '×', 'cdot': '·',
    'div': '÷', 'neq': '≠', 'ne': '≠', 'leq': '≤', 'le': '≤', 'geq': '≥',
    'ge': '≥', 'approx': '≈', 'equiv': '≡', 'sim': '∼', 'simeq': '≃',
    'propto': '∝', 'to': '→', 'rightarrow': '→', 'Rightarrow': '⇒',
    'leftarrow': '←', 'Leftarrow': '⇐', 'mapsto': '↦', 'ldots': '…',
    'dots': '…', 'cdots': '⋯', 'prime': '′', 'angle': '∠', 'deg': '°',
    'sum': '∑', 'prod': '∏', 'int': '∫', 'iint': '∬', 'oint': '∮',
    'hbar': 'ℏ', 'ell': 'ℓ', 'Re': 'Re', 'Im': 'Im',
}

# Superscript / subscript character maps
_SUP = str.maketrans('0123456789+-=()nist',
                     '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˢᵗ')
_SUB = str.maketrans('0123456789+-=()aehjkmnoprsxt',
                     '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕⱼₖₘₙₒₚᵣₛₓₜ')


def latex_to_text(expr: str) -> str:
    """Best-effort conversion of inline LaTeX into readable Unicode text."""
    s = expr.strip()
    if not s:
        return ''

    # \left / \right and spacing commands
    s = re.sub(r'\\(?:left|right|big|Big|bigg|Bigg)\b', '', s)
    s = re.sub(r'\\[,;:! ]', ' ', s)

    # \text{...} / \mathrm{...} -> contents
    for _ in range(8):
        new = re.sub(r'\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}',
                     r'\1', s)
        if new == s:
            break
        s = new

    # \frac -> a/b  (brace-less \frac12 form first)
    s = re.sub(r'\\[tdc]?frac\s*(\d)\s*(\d)', r'\\frac{\1}{\2}', s)

    def _frac(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        return f'{a}/{b}' if len(a) == 1 and len(b) == 1 else f'({a})/({b})'

    for _ in range(8):
        new = re.sub(r'\\[tdc]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}', _frac, s)
        if new == s:
            break
        s = new

    # \sqrt -> √(...)
    for _ in range(6):
        new = re.sub(r'\\[tdc]?sqrt\s*\{([^{}]*)\}', r'√(\1)', s)
        if new == s:
            break
        s = new

    s = re.sub(r'\\binom\s*\{([^{}]*)\}\s*\{([^{}]*)\}', r'C(\1,\2)', s)

    # superscripts / subscripts
    s = re.sub(r'\^\s*\{([^{}]*)\}', lambda m: m.group(1).translate(_SUP), s)
    s = re.sub(r'_\s*\{([^{}]*)\}', lambda m: m.group(1).translate(_SUB), s)
    s = re.sub(r'\^\s*(\w)', lambda m: m.group(1).translate(_SUP), s)
    s = re.sub(r'_\s*(\w)', lambda m: m.group(1).translate(_SUB), s)

    # \hat{x} / \bar{x} / \tilde{x} / \vec{x} -> x
    s = re.sub(r'\\(?:hat|bar|tilde|vec|dot)\s*\{([^{}]*)\}', r'\1', s)

    # remaining known commands
    def _cmd(m):
        name = m.group(1)
        return _SYMBOLS.get(name, name)

    s = re.sub(r'\\([A-Za-z]+)', _cmd, s)

    # drop escaped punctuation and leftover braces
    s = re.sub(r'\\(?=[^A-Za-z])', '', s)
    s = s.replace('{', '').replace('}', '')
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def extract_display_math(markdown: str) -> tuple[str, list[str]]:
    """Replace $$...$$ with ___LATEX_i___ placeholders.

    Returns (markdown_with_placeholders, [latex_source, ...]).
    """
    found: list[str] = []

    def _sub(m: re.Match) -> str:
        idx = len(found)
        found.append(m.group(1).strip())
        return f'\n\n___LATEX_{idx}___\n\n'

    return DISPLAY_MATH.sub(_sub, markdown), found


def split_into_blocks(markdown: str) -> list[str]:
    """Split markdown into logical blocks (paragraphs, headers, quotes, code blocks, etc.)."""
    blocks = []
    current_block = []
    in_code_block = False
    code_block_lines = []

    lines = markdown.split('\n')

    for line in lines:
        stripped = line.strip()

        # Handle code block boundaries
        if stripped.startswith('```'):
            if in_code_block:
                # End of code block
                in_code_block = False
                if code_block_lines:
                    # Mark as code block with special prefix for later processing
                    # Use ___CODE_BLOCK_START___ and ___CODE_BLOCK_END___ to preserve content
                    blocks.append('___CODE_BLOCK_START___' + '\n'.join(code_block_lines) + '___CODE_BLOCK_END___')
                code_block_lines = []
            else:
                # Start of code block
                if current_block:
                    blocks.append('\n'.join(current_block))
                    current_block = []
                in_code_block = True
            continue

        # If inside code block, collect ALL lines (including empty lines)
        if in_code_block:
            code_block_lines.append(line)
            continue

        # Empty line signals end of block
        if not stripped:
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            continue

        # Horizontal rule (divider) is its own block
        if re.match(r'^---+$', stripped):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            blocks.append('___DIVIDER___')
            continue

        # Headers, blockquotes are their own blocks
        if stripped.startswith(('#', '>')):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            blocks.append(stripped)
            continue

        # Standalone display-math placeholder is its own block
        if re.match(r'^___LATEX_\d+___$', stripped):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            blocks.append(stripped)
            continue

        # Image on its own line is its own block
        if re.match(r'^!\[.*\]\(.*\)$', stripped):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
            blocks.append(stripped)
            continue

        current_block.append(line)

    if current_block:
        blocks.append('\n'.join(current_block))

    # Handle unclosed code block
    if code_block_lines:
        blocks.append('___CODE_BLOCK_START___' + '\n'.join(code_block_lines) + '___CODE_BLOCK_END___')

    return blocks


def extract_images_and_dividers(markdown: str, base_path: Path,
                                latex_sources: list[str] | None = None
                                ) -> tuple[list[dict], list[dict], list[dict], str, int]:
    """Extract images, dividers and display-math blocks with their block index.

    All three share one block-index sequence, so images and LaTeX blocks stay
    correctly interleaved when they are inserted afterwards.

    Returns:
        (image_list, divider_list, latex_list, markdown_without_those, total_blocks)
    """
    blocks = split_into_blocks(markdown)
    images = []
    dividers = []
    latex_blocks = []
    clean_blocks = []

    img_pattern = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)$')
    latex_pattern = re.compile(r'^___LATEX_(\d+)___$')

    def tail_text() -> str:
        """Last line (<=80 chars) of the previous kept block, for positioning."""
        if not clean_blocks:
            return ""
        prev = clean_blocks[-1].strip()
        lines = [l for l in prev.split('\n') if l.strip()]
        return lines[-1][:80] if lines else ""

    for i, block in enumerate(blocks):
        block_stripped = block.strip()

        # Check for divider
        if block_stripped == '___DIVIDER___':
            block_index = len(clean_blocks)
            after_text = ""
            if clean_blocks:
                prev_block = clean_blocks[-1].strip()
                lines = [l for l in prev_block.split('\n') if l.strip()]
                after_text = lines[-1][:80] if lines else ""
            dividers.append({
                "block_index": block_index,
                "after_text": after_text
            })
            continue

        latex_match = latex_pattern.match(block_stripped)
        if latex_match and latex_sources is not None:
            idx = int(latex_match.group(1))
            if idx < len(latex_sources):
                latex_blocks.append({
                    "index": idx,
                    "latex": latex_sources[idx],
                    "block_index": len(clean_blocks),
                    "after_text": tail_text()
                })
                continue

        match = img_pattern.match(block_stripped)
        if match:
            alt_text = match.group(1)
            img_path = match.group(2)

            if not os.path.isabs(img_path):
                resolved_path = str(base_path / img_path)
            else:
                resolved_path = img_path

            filename = os.path.basename(urllib.parse.unquote(img_path))
            full_path, exists = find_image_file(resolved_path, filename)

            block_index = len(clean_blocks)

            after_text = ""
            if clean_blocks:
                prev_block = clean_blocks[-1].strip()
                lines = [l for l in prev_block.split('\n') if l.strip()]
                after_text = lines[-1][:80] if lines else ""

            images.append({
                "path": full_path,
                "original_path": resolved_path,
                "exists": exists,
                "alt": alt_text,
                "block_index": block_index,
                "after_text": after_text
            })
        else:
            clean_blocks.append(block)

    clean_markdown = '\n\n'.join(clean_blocks)
    return images, dividers, latex_blocks, clean_markdown, len(clean_blocks)


def extract_title(markdown: str) -> tuple[str, str]:
    """Extract title from first H1, H2, or first non-empty line.

    Returns:
        (title, markdown_without_title): Title string and markdown with H1 title removed.
        If title is from H1, it's removed from markdown to avoid duplication.
    """
    lines = markdown.strip().split('\n')
    title = "Untitled"
    title_line_idx = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # H1 - use as title and mark for removal
        if stripped.startswith('# '):
            title = stripped[2:].strip()
            title_line_idx = idx
            break
        # H2 - use as title but don't remove (it's a section header)
        if stripped.startswith('## '):
            title = stripped[3:].strip()
            break
        # First non-empty, non-image line
        if not stripped.startswith('!['):
            title = stripped[:100]
            break

    # Remove H1 title line from markdown to avoid duplication
    if title_line_idx is not None:
        lines.pop(title_line_idx)
        markdown = '\n'.join(lines)

    return title, markdown


_TAG_RE = re.compile(r'</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>')
_ENTITY_RE = re.compile(r'&(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});')


def escape_text_nodes(html: str) -> str:
    """Escape `<` / bare `&` that live in *text*, leaving real tags intact.

    Without this, mathematical prose such as `0<Re(s)<1` is emitted as a raw
    `<`, which the browser then parses as a tag and silently deletes everything
    up to the next `>`.
    """

    def fix(text: str) -> str:
        if not text:
            return text
        out = []
        i = 0
        while i < len(text):
            m = _ENTITY_RE.match(text, i)
            if m:
                out.append(text[i:m.end()])
                i = m.end()
                continue
            if text[i] == '&':
                out.append('&amp;')
                i += 1
                continue
            out.append(text[i])
            i += 1
        return ''.join(out).replace('<', '&lt;')

    result = []
    pos = 0
    for m in _TAG_RE.finditer(html):
        result.append(fix(html[pos:m.start()]))
        result.append(m.group(0))
        pos = m.end()
    result.append(fix(html[pos:]))
    return ''.join(result)


def markdown_to_html(markdown: str) -> str:
    """Convert markdown to HTML for X Articles rich text paste."""
    html = markdown

    # Process code blocks first (marked with ___CODE_BLOCK_START___ and ___CODE_BLOCK_END___)
    # Convert to blockquote format since X Articles doesn't support <pre><code>
    def convert_code_block(match):
        code_content = match.group(1)
        lines = code_content.strip().split('\n')
        # Join non-empty lines with <br> for display
        formatted = '<br>'.join(line for line in lines if line.strip())
        return f'<blockquote>{formatted}</blockquote>'

    html = re.sub(r'___CODE_BLOCK_START___(.*?)___CODE_BLOCK_END___', convert_code_block, html, flags=re.DOTALL)

    # Headers (H2 only, H1 is title)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)

    # Bold
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)

    # Italic
    html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)

    # Links
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)

    # Blockquotes (regular markdown blockquotes, not code blocks)
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)

    # Unordered lists
    html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)

    # Ordered lists
    html = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)

    # Wrap consecutive <li> in <ul>
    html = re.sub(r'((?:<li>.*?</li>\n?)+)', r'<ul>\1</ul>', html)

    # Paragraphs - split by double newlines
    parts = html.split('\n\n')
    processed_parts = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Skip if already a block element
        if part.startswith(('<h2>', '<h3>', '<blockquote>', '<ul>', '<ol>')):
            processed_parts.append(part)
        else:
            # Wrap in paragraph, convert single newlines to <br>
            part = part.replace('\n', '<br>')
            processed_parts.append(f'<p>{part}</p>')

    return escape_text_nodes(''.join(processed_parts))


def parse_markdown_file(filepath: str, math_mode: str = 'latex') -> dict:
    """Parse a markdown file and return structured data.

    math_mode:
        latex  - display math ($$..$$) -> latex_blocks[] for Insert > LaTeX;
                 inline math ($..$) -> Unicode text. (default)
        text   - every formula is flattened to Unicode text.
        keep   - leave $..$ / $$..$$ verbatim in the HTML.
        image  - leave formulas untouched (caller pre-rendered them to images).
    """
    path = Path(filepath)
    base_path = path.parent

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Skip YAML frontmatter if present
    if content.startswith('---'):
        end_marker = content.find('---', 3)
        if end_marker != -1:
            content = content[end_marker + 3:].strip()

    # Extract title first (and remove H1 from markdown)
    title, content = extract_title(content)

    # --- math -------------------------------------------------------------
    latex_sources: list[str] = []
    if math_mode == 'image':
        pass  # caller already replaced formulas with images
    elif math_mode == 'keep':
        pass  # leave the source delimiters alone
    elif math_mode == 'text':
        content = DISPLAY_MATH.sub(lambda m: latex_to_text(m.group(1)), content)
        content = INLINE_MATH.sub(lambda m: latex_to_text(m.group(1)), content)
    else:  # 'latex'
        content, latex_sources = extract_display_math(content)
        content = INLINE_MATH.sub(lambda m: latex_to_text(m.group(1)), content)

    # Extract images, dividers and display-math blocks with block indices
    (images, dividers, latex_blocks,
     clean_markdown, total_blocks) = extract_images_and_dividers(
        content, base_path, latex_sources)

    # Convert to HTML (text nodes are HTML-escaped there)
    html = markdown_to_html(clean_markdown)

    cover_image = images[0]["path"] if images else None
    cover_exists = images[0]["exists"] if images else True
    content_images = images[1:] if len(images) > 1 else []

    missing = [img for img in images if not img["exists"]]
    if missing:
        print(f"[parse_markdown] WARNING: {len(missing)} image(s) not found", file=sys.stderr)

    return {
        "title": title,
        "cover_image": cover_image,
        "cover_exists": cover_exists,
        "content_images": content_images,
        "latex_blocks": latex_blocks,
        "dividers": dividers,
        "html": html,
        "total_blocks": total_blocks,
        "math_mode": math_mode,
        "source_file": str(path.absolute()),
        "missing_images": len(missing)
    }


def main():
    parser = argparse.ArgumentParser(description='Parse Markdown for X Articles')
    parser.add_argument('file', help='Markdown file to parse')
    parser.add_argument('--output', choices=['json', 'html'], default='json',
                       help='Output format (default: json)')
    parser.add_argument('--html-only', action='store_true',
                       help='Output only HTML content')
    parser.add_argument('--math-mode', choices=['latex', 'text', 'keep', 'image'],
                       default='latex',
                       help='How to handle $..$ / $$..$$ (default: latex)')

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    result = parse_markdown_file(args.file, math_mode=args.math_mode)

    if args.html_only:
        print(result['html'])
    elif args.output == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result['html'])


if __name__ == '__main__':
    main()
