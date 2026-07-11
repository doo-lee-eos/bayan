"""
Run this LOCALLY, next to the file you downloaded from:
    https://kaikki.org/dictionary/Arabic/kaikki.org-dictionary-Arabic.jsonl

Usage:
    python3 filter_arabic_roots.py kaikki.org-dictionary-Arabic.jsonl roots_filtered.jsonl

What it does:
    Reads the full ~480MB JSONL file one line at a time (never loads it all
    into memory), pulls out only the fields useful for a root/lemma/gloss
    table, and only keeps entries that actually have a root recorded.
    Output is a much smaller JSONL you can upload to chat.
"""

import json
import re
import sys


def extract_root(entry):
    """
    Wiktextract records the root two different ways -- confirmed against
    real data:

      1. entry["etymology_templates"] containing an "ar-rootbox" template,
         whose args["1"] is a SINGLE space-separated string of the root
         letters, e.g. {"1": "ء ت ي"} for آتى -- not separate numbered
         args per letter.

      2. Some entries (e.g. إيتاء) have no ar-rootbox template at all --
         the root only appears inside senses[].categories[].name as a
         string like "Arabic terms belonging to the root ء ت ي". This is
         checked as a fallback when (1) isn't present.

    Returns the root as a plain string with spaces stripped (e.g. "ءتي"),
    or None if neither source has it.
    """
    for tmpl in entry.get("etymology_templates", []):
        if tmpl.get("name") == "ar-rootbox":
            root_str = tmpl.get("args", {}).get("1")
            if root_str:
                return root_str.replace(" ", "")

    for sense in entry.get("senses", []):
        for cat in sense.get("categories", []):
            name = cat.get("name", "")
            m = re.match(r"Arabic terms belonging to the root (.+)$", name)
            if m:
                return m.group(1).replace(" ", "")

    return None


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 filter_arabic_roots.py <input.jsonl> <output.jsonl>")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    kept = 0
    total = 0

    with open(in_path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:

        for line in fin:
            total += 1
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            root = extract_root(entry)
            if not root:
                continue

            glosses = []
            for sense in entry.get("senses", []):
                glosses.extend(sense.get("glosses", []))

            if not glosses:
                continue

            slim = {
                "word": entry.get("word"),
                "root": root,
                "pos": entry.get("pos"),
                "glosses": glosses,
            }
            fout.write(json.dumps(slim, ensure_ascii=False) + "\n")
            kept += 1

            if total % 200000 == 0:
                print(f"...processed {total} lines, kept {kept} so far", file=sys.stderr)

    print(f"Done. Processed {total} lines, kept {kept} root-tagged entries -> {out_path}")


if __name__ == "__main__":
    main()