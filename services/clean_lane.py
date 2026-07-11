import re

CITATION_TOKEN = re.compile(r'^\s*\*?\s*[A-Z][A-Za-z]{0,6}[.:]?\s*\*?\s*$')
ARABIC = r'[\u0600-\u06FF\u064B-\u0652\s]+'
OPENER_RE = re.compile(r'\b(He|She|It|They)\b')
CROSSREF_RE = re.compile(r'^\s*(and\s+\S+\s*)?:?\s*see\b', re.I)


def is_citation_paren(inner):
    tokens = inner.split(',')
    if not tokens or not inner.strip():
        return False
    return all(CITATION_TOKEN.match(t) for t in tokens if t.strip())


def strip_citation_parens(text):
    out, depth, start = [], 0, None
    for i, c in enumerate(text):
        if c == '(':
            if depth == 0:
                start = i
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0 and start is not None:
                if is_citation_paren(text[start + 1:i]):
                    out.append((start, i + 1))
                start = None
    result, last = [], 0
    for s, e in out:
        result.append(text[last:s]); last = e
    result.append(text[last:])
    return ''.join(result)


def first_clause_end(text, start_idx):
    """Scans from start_idx for the first '.', ';' or ':' that sits at
    paren/bracket depth 0 (relative to start_idx) and isn't part of
    'i.e.' -- avoids cutting off mid-citation like '(TA.' or '(a mountain ['."""
    depth = 0
    i = start_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in '([':
            depth += 1
        elif c in ')]':
            depth = max(0, depth - 1)
        elif depth == 0 and c in '.;:':
            if c == '.' and text[max(0, i - 1)] == 'e' and text[max(0, i - 3):i - 1] == 'i.':
                i += 1
                continue
            return i + 1
        i += 1
    return None


def clean_lane_gloss(raw, max_len=180):
    """Best-effort extraction of the primary gloss from a raw Lane's
    Lexicon entry. Returns (candidate, confident: bool). confident=False
    flags entries that likely need a hand-curated `short_meaning`
    override -- cross-references with no inline definition, or text that
    never matches the 'He/She/It/They ...' opening convention Lane's
    lexicon verb entries normally use."""

    if not raw:
        return None, False

    text = raw
    text = re.sub(r'^\s*\d+\s+', '', text)
    m = re.search(r'-[A-Za-z]\d+-', text)
    if m:
        text = text[:m.start()]

    text = strip_citation_parens(text)
    text = re.sub(rf'aor\.{ARABIC}?,', '', text)
    text = re.sub(rf'inf\. ?n\.{ARABIC}?(and{ARABIC})*,', '', text)
    text = re.sub(rf'syn\.{ARABIC}?:?', '', text)
    text = re.sub(r'&amp;?', '&', text)
    text = re.sub(r'^[\u0600-\u06FF\u064B-\u0652\s,]+(?=[A-Za-z\[])', '', text)
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\[\s*\]', '', text)
    stripped_lead = text.strip()

    if CROSSREF_RE.match(stripped_lead):
        return None, False  # pure cross-reference, nothing to clean here

    bracket = re.match(r'^\s*\[\s*(He|She|It|They|A)\b', text)
    if bracket:
        close = text.find(']')
        candidate = text[bracket.start():close].strip('[] ').strip() if close != -1 else None
    else:
        candidate = None
        om = OPENER_RE.search(text)
        if om:
            end = first_clause_end(text, om.start())
            candidate = text[om.start():end].strip() if end else text[om.start():om.start() + max_len].strip()

    confident = candidate is not None

    if not candidate:
        text2 = re.sub(r'\s+', ' ', text).strip(' ,;:[]')
        m3 = re.match(r'[^.]*\.', text2)
        candidate = (m3.group(0).strip() if m3 else text2[:max_len].strip())
        confident = False  # didn't match the normal opener pattern -- review

    candidate = re.sub(r'\s+', ' ', candidate).strip().rstrip(';,: ')

    if len(candidate) > max_len:
        cut = candidate[:max_len]
        last_break = max(cut.rfind(','), cut.rfind(';'))
        candidate = (cut[:last_break] if last_break > max_len * 0.4 else cut).rstrip(' ,;')

    if candidate and not candidate.endswith('.'):
        candidate += '.'

    if len(candidate) < 8 or 'aor' in candidate.lower():
        confident = False

    return candidate, confident