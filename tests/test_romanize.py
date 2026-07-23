import pytest

from lyrisync import romanize as rz


# -- hangul detection ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("안녕하세요", True),           # pure Korean
        ("Love me 사랑해 baby", True),  # mixed
        ("ㅋㅋㅋ", True),               # compatibility jamo only
        ("가", True),                  # first syllable code point
        ("힣", True),                  # last syllable code point
        ("hello world", False),
        ("12345 !?", False),
        ("こんにちは", False),           # Japanese is not hangul
        ("", False),
    ],
)
def test_contains_hangul(text, expected):
    assert rz.contains_hangul(text) is expected


# -- romanisation --------------------------------------------------------


def test_romanize_basic():
    assert rz.romanize_korean("안녕하세요") == "annyeonghaseyo"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("학교", "hakgyo"),            # ㄱ after ㄱ-batchim
        ("좋아", "joa"),               # ㅎ elision before vowel
        ("밥 먹었어?", "bap meogeosseo?"),  # batchim + liaison, punctuation kept
        ("어쩌면 잘된 일이야", "eojjeomyeon jaldoen iriya"),  # liaison across syllables
    ],
)
def test_romanize_sound_changes(text, expected):
    assert rz.romanize_korean(text) == expected


def test_mixed_line_keeps_english_words():
    assert rz.romanize_korean("Love me 사랑해 baby") == "Love me saranghae baby"


def test_non_korean_text_passes_through_unchanged():
    for text in ("hello world", "", "12345", "こんにちは"):
        assert rz.romanize_korean(text) == text


def test_library_failure_returns_original_line(monkeypatch):
    class Boom:
        def __init__(self, text):
            raise ValueError("weird line")

    monkeypatch.setattr(rz, "_Romanizer", Boom)
    assert rz.romanize_korean("안녕") == "안녕"
