# User lists (allow, block, lists_id; since 1.4.0). Validation cases mirror js/test/lists.test.ts: the same input gives the
# same list, index and reason in every package.
import itertools
import pickle
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import pytest

from newmean_pii import ListError, ModelFileError, Redactor, Span
from newmean_pii import _core
from newmean_pii._lists import REASONS, _read_list, make_lists
from newmean_pii._redactor import _source

from conftest import model_dir


def fail(tmp_path, **lists):
    """(list, index, reason) of the ListError that Redactor raises; tmp_path is an empty model_dir, so an accepted input
    would fail later with ModelFileError instead."""
    with pytest.raises(ListError) as e:
        Redactor("nano", "q4", model_dir=tmp_path, **lists)
    err = e.value
    assert isinstance(err, ValueError) and err.reason in REASONS
    return err.list, err.index, err.reason


def counts(allow=None, block=None):
    return make_lists(allow, block)[1:]


def test_list_shapes(tmp_path):
    assert fail(tmp_path, allow="제주") == ("allow", None, "bad_shape")  # a str is not read character by character
    assert fail(tmp_path, allow=b"x") == ("allow", None, "bad_shape")
    assert fail(tmp_path, allow=5) == ("allow", None, "bad_shape")
    assert fail(tmp_path, block="Kim") == ("block", None, "bad_shape")
    assert fail(tmp_path, block=("Kim", "PERSON")) == ("block", 0, "bad_shape")  # a tuple of two str is a list of two
    # no error for allow: "경기" and "PERSON", each for any label (-1); one labelled entry needs the list
    assert _read_list(("경기", "PERSON"), "allow") == [("경기".encode(), -1), (b"PERSON", -1)]
    assert _read_list([("경기", "PERSON")], "allow") == [("경기".encode(), 0)]
    assert counts(allow={"제주"}, block=iter([("Kim", "PERSON")])) == (1, 1)  # any iterable
    assert counts(allow=(x for x in ["a", ("b", None), ("c", "ORG")])) == (3, 0)
    assert make_lists(None, None) == (None, 0, 0) and make_lists([], ()) == (None, 0, 0)


def test_entry_shapes(tmp_path):
    assert fail(tmp_path, allow=["ok", 5]) == ("allow", 1, "bad_shape")
    assert fail(tmp_path, allow=[("x",)]) == ("allow", 0, "bad_shape")
    assert fail(tmp_path, allow=[["x", "ORG"]]) == ("allow", 0, "bad_shape")  # tuples only
    assert fail(tmp_path, allow=[("x", 5)]) == ("allow", 0, "bad_shape")
    assert fail(tmp_path, allow=[(None, "ORG")]) == ("allow", 0, "bad_shape")
    assert fail(tmp_path, block=["Kim"]) == ("block", 0, "bad_shape")
    assert fail(tmp_path, block=[("Kim", None)]) == ("block", 0, "bad_shape")  # the label is required
    assert fail(tmp_path, allow=[("x", "NAME")]) == ("allow", 0, "unknown_label")
    assert fail(tmp_path, block=[("x", "PERSON"), ("y", "person")]) == ("block", 1, "unknown_label")  # names, as given


def test_entry_texts(tmp_path):
    a = lambda text: fail(tmp_path, allow=[text])[1:]
    assert a("a" * 257) == (0, "too_long")
    assert a("\0" + "a" * 256) == (0, "too_long")  # the length bound comes first
    assert a("\0" + "😀" * 128) == (0, "too_long")  # 257 UTF-16 units (129 code points)
    assert a("\0" + "😀" * 127) == (0, "bad_text")  # 255 units
    assert a("\ud800" + "a" * 255) == (0, "bad_text")  # a lone surrogate counts one unit
    assert a("\ud800" + "a" * 256) == (0, "too_long")
    assert a("a" * 65) == (0, "too_long")
    assert a("😀" * 65) == (0, "too_long")
    for bad in ("\ud800", "\udc00x", "\ud83d\ude00", "a\nb", "a\rb", "a\x85b", "a\u2028b", "a\u2029b", "a\0b"):
        assert a(bad) == (0, "bad_text"), repr(bad)
    assert a("") == (0, "empty")
    for edge in (" x", "x ", "x\u3000", "\tx", "x\xa0", "\u2007x", "x\u202f"):
        assert a(edge) == (0, "edge_space"), repr(edge)
    assert counts(allow=["\u200bx", "a b", "a" * 64, "😀" * 64]) == (4, 0)  # U+200B is not White_Space


def test_error_never_holds_the_text(tmp_path):
    with pytest.raises(ListError) as e:
        Redactor("nano", "q4", model_dir=tmp_path, allow=["비밀이름 "])
    assert "비밀이름" not in str(e.value) and "비밀이름" not in repr(e.value.args)
    assert str(e.value) == "allow list entry 0: edge_space"
    copy = pickle.loads(pickle.dumps(e.value))
    assert (copy.list, copy.index, copy.reason) == ("allow", 0, "edge_space")


def test_order(tmp_path):
    # lists_id first, then allow, then block; within a list the first invalid entry
    assert fail(tmp_path, lists_id="", allow=[" x"]) == ("meta", None, "bad_lists_id")
    assert fail(tmp_path, allow=["ok", " x"], block=[("", "PERSON")]) == ("allow", 1, "edge_space")
    assert fail(tmp_path, allow=["ok"], block=[("ok", "PERSON"), ("", "PERSON")]) == ("block", 1, "empty")  # before in_both
    # too_many only after the first 10,000 are valid
    many = [f"v{i}" for i in range(10_001)]
    assert fail(tmp_path, allow=many) == ("allow", 10_000, "too_many")
    assert fail(tmp_path, allow=many[:50] + [" x"] + many[50:]) == ("allow", 50, "edge_space")
    assert fail(tmp_path, block=[(t, "ORG") for t in many]) == ("block", 10_000, "too_many")
    assert counts(allow=many[:10_000]) == (10_000, 0)
    # a list error comes before a sensitivity error (as in npm, Swift, Kotlin and Flutter)
    assert fail(tmp_path, block=[("", "PERSON")], sensitivity=float("nan")) == ("block", 0, "empty")
    assert fail(tmp_path, lists_id="", sensitivity=float("inf")) == ("meta", None, "bad_lists_id")


def test_unbounded_iterable_stops_at_10001(tmp_path):
    seen = itertools.count()

    def endless():
        for i in itertools.count():
            next(seen)
            yield f"v{i}"

    assert fail(tmp_path, allow=endless()) == ("allow", 10_000, "too_many")
    assert next(seen) == 10_001


def test_checks_across_entries_in_the_core(tmp_path):
    assert fail(tmp_path, allow=["seoul"], block=[("Kim", "PERSON"), ("KIM", "ORG"), ("SEOUL", "ORG")]) == ("block", 1, "label_conflict")
    assert fail(tmp_path, allow=["Seoul", ("Busan", "ORG")], block=[("x", "ORG"), ("BUSAN", "ORG")]) == ("block", 1, "in_both")
    # duplicates merge: same form and label; allow entries that differ only in label are one entry
    assert counts([("Seoul", "ADDRESS"), ("SEOUL", "ORG"), "Busan"], [("Kim", "PERSON"), ("KIM", "PERSON")]) == (2, 1)


def test_lists_id(tmp_path):
    for bad in ("", "a\nb", "a\rb", "\u2028", "\0", "\udc00", "x" * 129, 5, b"id"):
        assert fail(tmp_path, lists_id=bad) == ("meta", None, "bad_lists_id"), repr(bad)
    for good in ("survey-2026-11 v3", "x" * 128, " padded ", "😀" * 128):
        with pytest.raises(ModelFileError):  # accepted: the redactor goes on to load the model
            Redactor("nano", "q4", model_dir=tmp_path, lists_id=good)


def test_sources_are_mapped_explicitly():
    assert _source(_core.SOURCE_BLOCK) == "block" and _core.SOURCE_BLOCK == 8
    assert _source(_core.SOURCE_MODEL | _core.SOURCE_REGEX) == "model+regex"
    for bits in (0, 16, _core.SOURCE_BLOCK | _core.SOURCE_MODEL, _core.SOURCE_TABLE | _core.SOURCE_REGEX):
        with pytest.raises(RuntimeError, match="unknown span source"):
            _source(bits)


def test_core_lists_handle():
    lists = _core.lists_create([(b"x", -1), (b"X", 7)], [(b"Kim", 0)])
    assert isinstance(lists, _core.Lists) and lists.counts() == (1, 1)
    assert _core.lists_create([(b"x", -1)], [(b"X", 0)]) == ("block", 0, "in_both")
    assert _core.lists_create([(b"\xff", -1)], []) == ("allow", 0, "bad_text")  # the core's strict UTF-8 check
    assert _core.lists_create([], [(b"x", -1)]) == ("block", 0, "bad_shape")
    assert _core.Lists().counts() == (0, 0)  # no handle: no lists


def red(**kw):
    return Redactor("nano", "q4", model_dir=model_dir("nano"), **kw)


def test_block_whatever_labels(nano):
    with red(labels=[], block=[("박지훈", "PERSON")]) as r:
        assert r.detect("담당: 박지훈님께 문의") == [Span(4, 7, "PERSON", "block")]
        assert r.redact("담당: 박지훈님께 문의").text == "담당: [PERSON]님께 문의"
        assert r.detect("김박지훈 박지훈A") == [Span(5, 8, "PERSON", "block")]  # inside a word no; a word ends where Hangul meets Latin
        with pytest.raises(TypeError):
            r._core_model().detect("x", 0.0, 0, None, False, True, False, "not lists")


def test_block_before_korean_particles(nano):
    with red(block=[("박지훈", "PERSON"), ("새솔고등학교", "ORG"), ("Kim", "PERSON")]) as r:
        assert [s for s in r.detect("Kimberly") if s.source == "block"] == []
        assert r.detect("박지훈님께") == [Span(0, 3, "PERSON", "block")]
        text = "박지훈님께서는 새솔고등학교에서 일합니다"
        spans = r.detect(text)
        assert Span(0, 3, "PERSON", "block") in spans and Span(8, 14, "ORG", "block") in spans


def test_allow_removes_a_span(nano):
    text = "Hi, I'm Kim Minji, call me at 010-1234-5678. I live in Seoul."
    base = nano.detect(text)
    assert Span(8, 17, "PERSON", "model") in base
    rest = [s for s in base if s.start != 8]
    with red(allow=["Kim Minji"]) as r:
        assert r.detect(text) == rest
    with red(allow=[("kim minji", "PERSON_L2")]) as r:  # ASCII case folded; PERSON_L2 is the PERSON family
        assert r.detect(text) == rest
    with red(allow=[("Kim Minji", "ORG")]) as r:  # another family: kept
        assert r.detect(text) == base
    with red(allow=["Kim"]) as r:  # the whole span text must match
        assert r.detect(text) == base


def test_table_mode(nano):
    csv = "name,city\n박지훈,제주\n김민수,서울\n"
    with red(block=[("박지훈", "PERSON")], allow=["제주", "서울"]) as r:
        spans = r.detect_table(csv)
        assert Span(10, 13, "PERSON", "block") in spans
        assert not [s for s in spans if csv[s.start : s.end] in ("제주", "서울")]
        assert r.redact_table(csv).text.split("\n")[1] == "[PERSON],제주"
    with red(labels=[], block=[("박지훈", "PERSON")]) as r:
        assert r.detect_table(csv) == [Span(10, 13, "PERSON", "block")]
        assert r.detect_table(csv, context="header") == [Span(10, 13, "PERSON", "block")]  # header context runs without lists


def test_info_lists(nano):
    assert nano.info()["lists"] == {"allow": 0, "block": 0, "id": None}
    with red(allow=["Seoul", "SEOUL"], block=[("박지훈", "PERSON")], lists_id="v3") as r:
        assert r.info()["lists"] == {"allow": 1, "block": 1, "id": "v3"}
        assert r._lists.counts() == (1, 1)


def test_nfd_text_does_not_match(nano):
    with red(labels=[], block=[("박지훈", "PERSON")]) as r:
        nfd = unicodedata.normalize("NFD", "박지훈님께")
        assert nfd != "박지훈님께" and r.detect(nfd) == []
        assert r.detect(unicodedata.normalize("NFC", nfd)) == [Span(0, 3, "PERSON", "block")]


def test_lists_shared_across_threads_and_close(nano):
    texts = [f"{i}번 답변: 박지훈님께 {i}일에 연락" for i in range(40)]
    with red(block=[("박지훈", "PERSON")]) as r:
        want = [r.detect(t) for t in texts]
        with ThreadPoolExecutor(8) as pool:
            assert list(pool.map(r.detect, texts)) == want
        lists = r._lists
        started = threading.Event()

        def run():
            started.set()
            return [r.detect(t) for t in texts]

        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(run)
            started.wait()
            r.close()  # calls in flight hold their own references to the model and the lists
            try:
                assert fut.result() == want
            except RuntimeError as e:
                assert "closed" in str(e)
        assert lists.counts() == (0, 1)
        with pytest.raises(RuntimeError, match="closed"):
            r.detect(texts[0])
