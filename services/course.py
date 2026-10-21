"""
The "Learn Qur'anic Arabic" course.

Lessons live as JSON files in the project's course/ folder (course.json lists
the levels; level1.json, level2.json ... hold the lessons), so the teaching
text can be edited without touching code. A level marked "hidden": true in
course.json is left out of the course (its lessons are still checked).

Every Qur'anic word a lesson shows is checked when the course loads:
  * "examples" name a word and its verse ("word": "قُلْ", "ref": "112:1");
    the word must be in that verse.
  * "family", "pattern", "scale", "builder" and the drills name a root and a
    pattern (["كتب", "فَاعِل"]); services/patterns.py builds the word and it
    must match Bayan's own entry for that root and pattern.
If anything doesn't match, the course refuses to load and says which lesson
and word is wrong, so a typo shows up straight away instead of on a page.

Lesson text uses a tiny markup:
    **bold**  *italic*  {{stat:name}}  (a figure counted from the databases)
Arabic inside text is wrapped automatically so it gets the Arabic font.

Quizzes: a lesson's "quiz" holds hand-written questions, and its "drill"
lists root + pattern pairs from which questions are generated (find the
root, name the pattern, build the word, work out the meaning). The page picks
a fresh set from the pool on every attempt.
"""
import itertools
import json
import random
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from markupsafe import Markup, escape

from services.database import get_connection
from services import patterns as P

COURSE_DIR = Path(__file__).resolve().parent.parent / "course"

_AR = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
ARABIC_RUN = re.compile(rf"([{_AR}][{_AR}\s‌‍.()،ـ]*[{_AR}])|([{_AR}])")


class CourseError(Exception):
    pass


def _canon(text):
    """Same word, whatever order its marks were typed in (shadda first)."""
    return P.canon(text)


# ----------------------------------------------------------------- stats
def _stats(cur):
    one = lambda sql, *a: cur.execute(sql, a).fetchone()[0]
    words = one("SELECT COUNT(*) FROM words")
    affixed = one("SELECT COUNT(DISTINCT word_id) FROM morphemes "
                  "WHERE features LIKE '%PREF%' OR (features LIKE '%SUFF%' AND surface <> '')")
    roots = one("SELECT COUNT(DISTINCT root) FROM morphemes WHERE root IS NOT NULL AND root <> ''")

    def prefix(lem, kind=None):
        sql = "SELECT COUNT(*) FROM morphemes WHERE features LIKE '%PREF%' AND features LIKE ?"
        args = ["%|LEM:" + lem]
        if kind:
            sql += " AND features LIKE ?"
            args.append(kind + "|%")
        return one(sql, *args)

    ps = P.pattern_stats()
    return {
        "words": f"{words:,}",
        "affix_pct": f"{round(100 * affixed / words)}%",
        "roots": f"{roots:,}",
        "patterns_total": str(ps["patterns_total"]),
        "top10_pct": f"{ps['top10']}%",
        "top20_pct": f"{ps['top20']}%",
        "waw": f"{prefix('و'):,}",
        "fa": f"{prefix('ف'):,}",
        "bi": f"{prefix('ب', 'P'):,}",
        "li": f"{prefix('ل', 'P'):,}",
        "ka": f"{prefix('ك', 'P'):,}",
    }


# ------------------------------------------------------------- markup
def render_md(text, stats):
    def stat(m):
        key = m.group(1)
        if key not in stats:
            raise CourseError(f"unknown stat {{{{stat:{key}}}}}")
        return stats[key]

    text = re.sub(r"\{\{stat:(\w+)\}\}", stat, text or "")
    out = str(escape(text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", out)
    out = ARABIC_RUN.sub(lambda m: f'<span class="ar" lang="ar" dir="rtl">{m.group(0)}</span>', out)
    return Markup(out)


def ar(text):
    return Markup(f'<span class="ar" lang="ar" dir="rtl">{escape(text)}</span>')


def search_href(word):
    return "/search?q=" + quote(word)


def word_html(parts):
    """A built word with its root letters marked, so the root stands out."""
    out = []
    for text, slot in parts:
        cls = f"rl s{slot}" if slot is not None else "pl"
        out.append(f'<span class="{cls}">{escape(text)}</span>')
    return Markup('<span class="pw" lang="ar" dir="rtl">' + "".join(out) + "</span>")


# ----------------------------------------------------------- patterns
class Ctx:
    """What a lesson knows: the pattern glossary and its own gloss overrides."""

    def __init__(self, glossary, lesson, where):
        self.glossary = glossary
        self.glosses = lesson.get("glosses", {})
        self.root_means = lesson.get("roots", {})
        self.where = where

    def pattern_info(self, wazn):
        for key, info in self.glossary.items():
            if P.same_pattern(key, wazn):
                return dict(info, wazn=key)
        raise CourseError(f"{self.where}: pattern {wazn} is not in the glossary in course.json")

    def word(self, root, wazn, required=True):
        try:
            w = P.find_word(root, wazn)
        except P.PatternError as e:
            raise CourseError(f"{self.where}: {e}")
        if w is None:
            if required:
                raise CourseError(f"{self.where}: the Qur'an has no {P.build(root, wazn)[0]} "
                                  f"({P.spaced(root)} + {wazn})")
            return None
        w = dict(w)
        key = f"{root}|{wazn}"
        if key in self.glosses:
            w["gloss"] = self.glosses[key]
        info = self.pattern_info(wazn)
        w.update(html=word_html(w["parts"]), href=search_href(w["lemma"]),
                 wazn=info["wazn"], wazn_say=info.get("say", ""), role=info.get("role", ""),
                 root_spaced=P.spaced(root))
        return w

    def plural(self, root, sing, pl, gloss, own=False):
        try:
            w = P.find_plural(root, sing, pl, own_entry=own)
        except P.PatternError as e:
            raise CourseError(f"{self.where}: {e}")
        if w is None:
            raise CourseError(f"{self.where}: the Qur'an has no {P.build(root, pl)[0]} as a plural of "
                              f"{P.build(root, sing)[0]}")
        w = dict(w, gloss=gloss)
        info = self.pattern_info(pl)
        s = self.word(root, sing)
        w.update(html=word_html(w["parts"]), href=search_href(w["surfaces"][0]),
                 wazn=info["wazn"], wazn_say=info.get("say", ""), role=info.get("role", ""),
                 root_spaced=P.spaced(root), sing=s)
        return w

    def entry(self, item):
        """A drill or table entry: [root, pattern] or [root, singular, plural, gloss(, own)]."""
        if len(item) == 2:
            return self.word(*item)
        return self.plural(item[0], item[1], item[2], item[3], own=len(item) > 4 and bool(item[4]))

    def group(self, wazn):
        return self.pattern_info(wazn).get("group", "noun")

    def root_meaning(self, root):
        """(verb word or None, meaning text) for a root."""
        if root in self.root_means:
            return None, self.root_means[root]
        v = P.root_verb(root)
        if v:
            key = f"{root}|{v['wazn']}"
            return v, self.glosses.get(key, v["gloss"])
        return None, None


# Kinds of word whose meanings overlap too much to be fair wrong answers for each other.
CLASHES = {"doer": {"quality"}, "quality": {"doer"}}


def _family(ctx, b):
    root = b["root"]
    items = [ctx.word(root, w) for w in b["patterns"]]
    verb, means = ctx.root_meaning(root)
    return {"root_spaced": P.spaced(root), "root": root, "means": means, "items": items,
            "root_href": search_href("√" + root)}


def _pattern(ctx, b):
    info = ctx.pattern_info(b["wazn"])
    rows = []
    for root in b["roots"]:
        w = ctx.word(root, b["wazn"])
        verb, means = ctx.root_meaning(root)
        rows.append({"word": w, "verb": verb, "means": means})
    return {"wazn": info["wazn"], "say": info.get("say", ""), "role": info.get("role", ""),
            "pattern_html": word_html(P.pattern_parts(info["wazn"])), "rows": rows}


def _plurals(ctx, b):
    info = ctx.pattern_info(b["pattern"])
    rows = [ctx.plural(it[0], it[1], b["pattern"], it[2], own=len(it) > 3 and bool(it[3])) for it in b["items"]]
    return {"wazn": info["wazn"], "say": info.get("say", ""),
            "pattern_html": word_html(P.pattern_parts(info["wazn"])), "rows": rows}


def _scale(ctx, b):
    out = []
    for root, wazn in b["items"]:
        w = ctx.word(root, wazn)
        pat = P.pattern_parts(wazn)
        cols = []
        for (pt, ps), (wt, ws) in zip(pat, w["parts"]):
            cols.append({"pattern": pt, "word": wt, "slot": ps})
        out.append({"word": w, "cols": cols})
    return out


def _builder(ctx, b):
    roots, pats = b["roots"], b["patterns"]
    for r in roots:
        if not P.is_sound(r):
            raise CourseError(f"{ctx.where}: builder roots must be sound (no و ي or hamza): {r}")
    combos = {}
    for r in roots:
        for p in pats:
            word, parts = P.build(r, p)
            w = ctx.word(r, p, required=False)
            combos[f"{r}|{p}"] = {
                "html": str(word_html(parts)), "word": word,
                "found": bool(w), "gloss": w["gloss"] if w else "",
                "freq": w["freq"] if w else 0, "href": w["href"] if w else "",
            }
    return {
        "roots": [{"key": r, "label": P.spaced(r), "means": ctx.root_meaning(r)[1] or ""} for r in roots],
        "patterns": [{"key": p, "label": P.canon(p), "role": ctx.pattern_info(p).get("role", "")} for p in pats],
        "combos": combos,
    }


# ------------------------------------------------------------- drills
def _opt(text):
    return str(ar(text))


def _drill_pool(ctx, spec, stats):
    """Every question the drill can ask, each with the right answer first."""
    pairs = spec["words"]
    kinds = spec.get("kinds", ["root", "pattern", "build", "meaning"])
    pats = []
    for p in [it[1] if len(it) == 2 else it[2] for it in pairs] + spec.get("patterns", []):
        if not any(P.same_pattern(p, q) for q in pats):
            pats.append(p)
    words = [ctx.entry(it) for it in pairs]
    rng = random.Random(1)
    pool = []

    for w in words:
        root, wazn = w["root"], w["wazn"]
        letters = [b for b, _ in P.units(w["word"])]
        rl = P.root_letters(root)
        slots = (f"{ar(rl[0])} in the {ar('ف')} slot, {ar(rl[1])} in the {ar('ع')} slot "
                 f"and {ar(rl[2])} in the {ar('ل')} slot")
        added = list(dict.fromkeys(t[0] for t, s in w["parts"] if s is None))

        # 1. find the root -- the traps are three letters taken from the word itself
        if "root" in kinds and P.is_sound(root) and len(letters) >= 4:
            combos = []
            for c in itertools.combinations(range(len(letters)), 3):
                cand = [letters[i] for i in c]
                if (cand != rl and len(set(cand)) == 3 and "ا" not in cand and "ة" not in cand
                        and cand not in combos):
                    combos.append(cand)
            rng.shuffle(combos)
            traps = combos[:3]
            if len(traps) >= 2:
                names = [str(ar(a)) for a in added]
                listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
                extra = listed + (" is" if len(names) == 1 else " are") + " part of the pattern. "
                pool.append({
                    "kind": "root",
                    "q_html": f"What is the root of {ar(w['word'])}?",
                    "options_html": [_opt(" ".join(rl))] + [_opt(" ".join(t)) for t in traps],
                    "explain_html": f"{w['html']} is on {ar(wazn)}: {slots}. {extra}"
                                    f"So the root is {ar(' '.join(rl))}.",
                })

        # 2. name the pattern
        if "pattern" in kinds:
            others = [p for p in pats if not P.same_pattern(p, wazn)]
            rng.shuffle(others)
            if len(others) >= 2:
                pool.append({
                    "kind": "pattern",
                    "q_html": f"Which pattern is {ar(w['word'])} on?",
                    "options_html": [_opt(wazn)] + [_opt(P.canon(p)) for p in others[:3]],
                    "explain_html": f"{w['html']} has {slots}; everything else, letters and vowels, "
                                    f"is the pattern {ar(wazn)}.",
                })

        # 3. build the word
        if "build" in kinds and P.is_sound(root):
            others = [p for p in pats if not P.same_pattern(p, wazn)]
            rng.shuffle(others)
            built = [x for x in dict.fromkeys(P.build(root, p)[0] for p in others) if x != w["word"]][:3]
            if len(built) >= 2:
                pool.append({
                    "kind": "build",
                    "q_html": f"Put the root {ar(' '.join(rl))} into the pattern {ar(wazn)}.",
                    "options_html": [_opt(w["word"])] + [_opt(x) for x in built],
                    "explain_html": f"Put {slots}, and keep the rest of {ar(wazn)} as it is: "
                                    f"{w['html']}, “{escape(w['gloss'])}”.",
                })

        # 4. work out the meaning from root + pattern. The wrong answers are
        #    real words from the same root, so the pattern is what decides it.
        if "meaning" in kinds and "sing" not in w:
            verb, means = ctx.root_meaning(root)
            if means and not (verb and verb["word"] == w["word"]):
                # One wrong answer per kind of word, never from a kind that could
                # also fit (a doer and a quality word often mean much the same).
                mine = ctx.group(wazn)
                clash = {mine} | CLASHES.get(mine, set())
                by_group = {}
                for p in pats:
                    g = ctx.group(p)
                    if g in clash or g in by_group:
                        continue
                    o = ctx.word(root, p, required=False)
                    if o and o["gloss"] and o["gloss"].lower() != w["gloss"].lower():
                        by_group[g] = o["gloss"]
                if len(by_group) < 3 and verb and "verb" not in clash:
                    by_group.setdefault("verb", means if means.startswith("to ") else "to " + means)
                glosses = list(dict.fromkeys(by_group.values()))
                if len(glosses) >= 2:
                    rng.shuffle(glosses)
                    lead = (f"{ar(verb['word'])} means “{escape(means)}”." if verb
                            else f"The root {ar(' '.join(rl))} is about {escape(means)}.")
                    pool.append({
                        "kind": "meaning",
                        "q_html": f"{lead} What does {ar(w['word'])} mean?",
                        "options_html": [str(escape(w["gloss"]))] + [str(escape(g)) for g in glosses[:3]],
                        "explain_html": f"{w['html']} is on {ar(wazn)}, {escape(w['role'])}: "
                                        f"“{escape(w['gloss'])}”.",
                    })
    for q in pool:
        q["answer"] = 0
        q["q_html"] = str(q["q_html"])
        q["explain_html"] = str(q["explain_html"])
    return pool


def _prepare_quiz(quiz, stats, where):
    out = []
    for i, q in enumerate(quiz or []):
        opts = q.get("options") or []
        ans = q.get("answer")
        if not isinstance(ans, int) or not 0 <= ans < len(opts) or len(opts) < 2:
            raise CourseError(f"{where}: quiz question {i + 1} has a bad answer index")
        out.append({
            "kind": "fixed",
            "q_html": str(render_md(q.get("q"), stats)),
            "options_html": [str(render_md(o, stats)) for o in opts],
            "answer": ans,
            "explain_html": str(render_md(q.get("explain", ""), stats)),
        })
    return out


# ----------------------------------------------------------- examples
def _resolve_example(cur, item, where):
    word = item.get("word")
    ref = item.get("ref")
    if not word or not ref or not re.fullmatch(r"\d+:\d+", ref):
        raise CourseError(f"{where}: an example needs \"word\" and \"ref\" (surah:ayah), got {item}")
    surah, ayah = map(int, ref.split(":"))
    rows = cur.execute(
        "SELECT word_number, surface FROM words WHERE surah = ? AND ayah = ? ORDER BY word_number",
        (surah, ayah)).fetchall()
    if not rows:
        raise CourseError(f"{where}: verse {ref} not found")
    hit = [wn for wn, s in rows if _canon(s) == _canon(word)]
    if not hit:
        raise CourseError(f"{where}: {word} is not in {ref} (that verse has: {' '.join(s for _, s in rows)})")
    wn = hit[0]
    tr = cur.execute("SELECT text FROM translations WHERE surah = ? AND ayah = ? AND translator = 'sahih_international'",
                     (surah, ayah)).fetchone()
    surface = dict(rows)[wn]
    return {
        "word": surface,
        "ref": ref,
        "surah": surah,
        "ayah": ayah,
        "say": item.get("say", ""),
        "means": item.get("means", ""),
        "note": item.get("note", ""),
        "verse": [{"text": s, "hit": n == wn} for n, s in rows],
        "translation": tr[0] if tr else "",
    }


LETTERS = [
    ("ا", "alif", "ā / a", False), ("ب", "bāʾ", "b", True), ("ت", "tāʾ", "t", True),
    ("ث", "thāʾ", "th (think)", True), ("ج", "jīm", "j", True), ("ح", "ḥāʾ", "ḥ (breathy h)", True),
    ("خ", "khāʾ", "kh (Scottish loch)", True), ("د", "dāl", "d", False), ("ذ", "dhāl", "dh (this)", False),
    ("ر", "rāʾ", "r (rolled)", False), ("ز", "zāy", "z", False), ("س", "sīn", "s", True),
    ("ش", "shīn", "sh", True), ("ص", "ṣād", "ṣ (heavy s)", True), ("ض", "ḍād", "ḍ (heavy d)", True),
    ("ط", "ṭāʾ", "ṭ (heavy t)", True), ("ظ", "ẓāʾ", "ẓ (heavy dh)", True), ("ع", "ʿayn", "ʿ (from the throat)", True),
    ("غ", "ghayn", "gh (French r)", True), ("ف", "fāʾ", "f", True), ("ق", "qāf", "q (deep k)", True),
    ("ك", "kāf", "k", True), ("ل", "lām", "l", True), ("م", "mīm", "m", True), ("ن", "nūn", "n", True),
    ("ه", "hāʾ", "h", True), ("و", "wāw", "w / ū", False), ("ي", "yāʾ", "y / ī", True),
]


def _prepare_block(cur, ctx, block, stats, where):
    b = dict(block)
    kind = b.get("type")
    if kind in ("text", "tip"):
        b["html"] = render_md(b.get("md"), stats)
    elif kind == "heading":
        b["html"] = render_md(b.get("md"), stats)
    elif kind == "table":
        b["head_html"] = [render_md(h, stats) for h in b.get("head", [])]
        b["rows_html"] = [[render_md(c, stats) for c in row] for row in b.get("rows", [])]
    elif kind == "examples":
        b["items"] = [_resolve_example(cur, it, where) for it in b.get("items", [])]
        for it in b["items"]:
            it["note_html"] = render_md(it["note"], stats)
    elif kind == "letters":
        b["letters"] = [
            {"letter": l, "name": n, "sound": s, "joins": j,
             "forms": (l, l + "ـ", "ـ" + l + "ـ", "ـ" + l) if j else (l, l, "ـ" + l, "ـ" + l)}
            for l, n, s, j in LETTERS
        ]
    elif kind == "family":
        b["family"] = _family(ctx, b)
        b["note_html"] = render_md(b.get("note", ""), stats)
    elif kind == "pattern":
        b["pattern"] = _pattern(ctx, b)
        b["note_html"] = render_md(b.get("note", ""), stats)
    elif kind == "scale":
        b["scale"] = _scale(ctx, b)
    elif kind == "plurals":
        b["plurals"] = _plurals(ctx, b)
        b["note_html"] = render_md(b.get("note", ""), stats)
    elif kind == "builder":
        b["builder"] = _builder(ctx, b)
        b["note_html"] = render_md(b.get("note", ""), stats)
    else:
        raise CourseError(f"{where}: unknown block type {kind!r}")
    return b


def _load_lesson(cur, glossary, ls, stats, where):
    ctx = Ctx(glossary, ls, where)
    blocks = [_prepare_block(cur, ctx, b, stats, where) for b in ls.get("blocks", [])]
    fixed = _prepare_quiz(ls.get("quiz"), stats, where)
    pool = _drill_pool(ctx, ls["drill"], stats) if ls.get("drill") else []
    count = (ls.get("drill") or {}).get("count", 0)
    if ls.get("drill") and len(pool) < count:
        raise CourseError(f"{where}: the drill can only make {len(pool)} questions but asks for {count}")
    return blocks, {"fixed": fixed, "pool": pool, "count": count + len(fixed)}


@lru_cache(maxsize=1)
def load_course():
    """The whole course, every example checked against the databases."""
    meta = json.loads((COURSE_DIR / "course.json").read_text(encoding="utf-8"))
    glossary = meta.get("patterns", {})
    conn = get_connection()
    cur = conn.cursor()
    stats = _stats(cur)
    levels, lessons, order = [], {}, []
    for lv in meta["levels"]:
        if lv.get("hidden"):
            # Switched off in course.json ("hidden": true). Its lessons are
            # still checked, so they're ready to bring back, but they don't
            # appear in the course and their pages don't open.
            if lv.get("file"):
                data = json.loads((COURSE_DIR / lv["file"]).read_text(encoding="utf-8"))
                for ls in data["lessons"]:
                    _load_lesson(cur, glossary, ls, stats, f"hidden lesson {ls.get('number')} ({ls.get('slug')})")
            continue
        level = {k: lv[k] for k in ("number", "title", "summary")}
        level["summary_html"] = render_md(lv["summary"], stats)
        level["coming_soon"] = bool(lv.get("coming_soon"))
        level["lessons"] = []
        if lv.get("file"):
            data = json.loads((COURSE_DIR / lv["file"]).read_text(encoding="utf-8"))
            for ls in data["lessons"]:
                where = f"lesson {ls.get('number')} ({ls.get('slug')})"
                if ls["slug"] in lessons:
                    raise CourseError(f"{where}: duplicate slug")
                blocks, quiz = _load_lesson(cur, glossary, ls, stats, where)
                lesson = {
                    "slug": ls["slug"], "number": ls["number"], "title": ls["title"],
                    "title_html": render_md(ls["title"], stats),
                    "minutes": ls.get("minutes"), "goal": ls.get("goal", ""),
                    "goal_html": render_md(ls.get("goal", ""), stats),
                    "level": lv["number"], "level_title": lv["title"],
                    "blocks": blocks, "quiz": quiz,
                }
                lessons[lesson["slug"]] = lesson
                order.append(lesson["slug"])
                level["lessons"].append(lesson)
        levels.append(level)
    conn.close()
    for i, slug in enumerate(order):
        lessons[slug]["prev"] = lessons[order[i - 1]] if i > 0 else None
        lessons[slug]["next"] = lessons[order[i + 1]] if i + 1 < len(order) else None
    return {"title": meta["title"], "intro_html": render_md(meta.get("intro", ""), stats),
            "levels": levels, "lessons": lessons, "order": order,
            "titles": {s: f"{lessons[s]['number']} {lessons[s]['title']}" for s in order}}


def get_lesson(slug):
    return load_course()["lessons"].get(slug)
