import re

MIN_SCORE = 0.85
DUP_BITS = 6

_engine = None
_seen = []


def load():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine = RapidOCR(params={"Global.log_level": "warning"})
    return _engine


def unload():
    global _engine
    _engine = None
    _seen.clear()


def _dhash(im):
    small = im.convert("L").resize((9, 8))
    px = list(small.getdata())
    return sum(1 << i for i in range(64) if px[i // 8 * 9 + i % 8] > px[i // 8 * 9 + i % 8 + 1])


def ocr(path):
    from PIL import Image
    with Image.open(path) as im:
        h = _dhash(im)
    for prev, text in _seen:
        if bin(prev ^ h).count("1") <= DUP_BITS:
            return text
    r = load()(str(path))
    lines = [t.strip() for t, s in zip(r.txts or [], r.scores or []) if float(s) >= MIN_SCORE and _plausible(t)]
    text = "\n".join(lines)
    _seen.append((h, text))
    del _seen[:-20]
    return text


def _plausible(text):
    letters = sum(c.isalpha() for c in text)
    return letters >= 3 and letters / max(len(text.replace(" ", "")), 1) >= 0.6


def dedupe_lines(texts):
    seen, out = set(), []
    for text in texts:
        for line in (text or "").splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            key = re.sub(r"\W+", "", line.lower())
            if len(key) < 3 or key in seen:
                continue
            seen.add(key)
            out.append(line)
    return "\n".join(out)
