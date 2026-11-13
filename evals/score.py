import difflib
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
D = Path("data/experiments")
REF_MODEL = "gemini-3.8-flash"
VARIANTS = ["lowres", "jpeg", "rotated", "tiny"]
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
KEY = {
    1: ["Zwiebeln", "Pfeffer", "Nelken", "Salz", "Lorbeerblätter", "Essig", "Wasser",
        "Sauerbraten", "Josefines", "1887", "Bratenstücke"],
    2: ["Speck", "Butter", "Mehl", "Zucker", "Rotwein", "Korinthen", "Rosinen", "Äpfel",
        "Möhren", "Wacholderbeeren", "Essigwasser", "Tagen", "Stunde"],
}


def load(path):
    lines = [l.split("\t", 1)[-1] for l in path.read_text(encoding="utf-8").splitlines()]
    return lines


def norm(lines):
    text = "\n".join(lines)
    text = re.sub(r"-\s*\n\s*(\[drawing\]\s*)?", "", text)  # join line-end hyphens
    text = text.replace("[drawing] ", "")
    text = re.sub(r"[‐‑–—]", "-", text)
    return " ".join(text.split())


def digits(text):
    return Counter(re.findall(r"\d+(?:/\d+)?\.?", text))


rows = []
for v in VARIANTS:
    for m in MODELS:
        sims, key_hit, key_all, dmiss, dextra, markers, nlines, novel = [], 0, 0, [], [], 0, 0, []
        ok = True
        for p in (1, 2):
            f = D / "stress" / f"Recipe_{v}_{m}_p{p}.txt"
            if not f.exists():
                ok = False
                break
            ref = norm(load(D / f"Recipe_{REF_MODEL}_p{p}.txt"))
            got_lines = load(f)
            got = norm(got_lines)
            sims.append(difflib.SequenceMatcher(None, ref.split(), got.split(), autojunk=False).ratio())
            for k in KEY[p]:
                key_all += 1
                key_hit += k in got
            rd, gd = digits(ref), digits(got)
            dmiss += [f"p{p}:{k}" for k in (rd - gd)]
            dextra += [f"p{p}:{k}" for k in (gd - rd)]
            markers += got.count("[?")
            nlines += len(got_lines)
            refwords = {w.strip(".,;:!?()").lower() for w in ref.split()}
            novel += [w for w in got.split() if w.strip(".,;:!?()").lower() not in refwords and "[?" not in w][:12]
        if not ok:
            rows.append((v, m, "MISSING (run failed or not finished)"))
            continue
        rows.append((v, m, f"similarity {sum(sims)/2:.3f} | key words {key_hit}/{key_all} | "
                           f"numbers missing {dmiss or '-'} extra {dextra or '-'} | "
                           f"[?] markers {markers} | lines {nlines} | new words {novel[:8] or '-'}"))
for v, m, s in rows:
    print(f"{v:8} {m:24} {s}")
