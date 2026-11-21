import pytest

from types import SimpleNamespace

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

from app.ml import ocr


def _frame(path, shade):
    im = Image.new("RGB", (90, 160), (shade, shade, shade))
    ImageDraw.Draw(im).rectangle((10, 10, 40, 60), fill=(255 - shade, 0, 0))
    im.save(path)
    return path


def test_ocr_filters_and_skips_duplicate_frames(tmp_path, monkeypatch):
    calls = []

    def engine(path):
        calls.append(path)
        return SimpleNamespace(txts=["spora yeni başladığımız", "939", "VSVN", "faint text"],
                               scores=[0.99, 1.0, 0.99, 0.5])

    monkeypatch.setattr(ocr, "load", lambda: engine)
    ocr.unload()
    a = _frame(tmp_path / "a.jpg", 30)
    b = _frame(tmp_path / "b.jpg", 31)
    assert ocr.ocr(a) == "spora yeni başladığımız\nVSVN"
    assert ocr.ocr(b) == "spora yeni başladığımız\nVSVN"
    assert len(calls) == 1
    ocr.unload()


def test_ocr_lines_keep_first_seen_time(monkeypatch):
    from app.stages import extract
    texts = {"a": "SALE\nfoo bar", "b": "sale!\nnew line", "c": ""}
    monkeypatch.setattr(extract.ocr_ml, "ocr", lambda p: texts[p])
    assert extract.ocr_lines([(0.0, "a"), (0.5, "b"), (1.0, "c")]) == [[0.0, "SALE"], [0.0, "foo bar"],
                                                                       [0.5, "new line"]]
