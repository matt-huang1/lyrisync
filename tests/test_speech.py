from lyrisync import speech as sp


SAY_OUTPUT_WITH_YUNA = """\
Alex                en_US    # Most people recognize me by my voice.
Bad News            en_US    # The light you see at the end of the tunnel...
Kyoko               ja_JP    # こんにちは、私の名前はKyokoです。
Ting-Ting           zh_CN    # 您好，我叫Ting-Ting。
Yuna                ko_KR    # 안녕하세요. 제 이름은 유나입니다.
"""

SAY_OUTPUT_ENHANCED_ONLY = """\
Alex                en_US    # Most people recognize me by my voice.
Yuna (Enhanced)     ko_KR    # 안녕하세요. 제 이름은 유나입니다.
"""

SAY_OUTPUT_WITHOUT_YUNA = """\
Alex                en_US    # Most people recognize me by my voice.
Kyoko               ja_JP    # こんにちは、私の名前はKyokoです。
YunaX               ko_KR    # an imposter voice
"""


# -- voice availability parsing ------------------------------------------


def test_parses_voice_names_including_multiword():
    names = sp.parse_voice_names(SAY_OUTPUT_WITH_YUNA)
    assert "Yuna" in names
    assert "Bad News" in names  # single spaces inside a name survive
    assert "Ting-Ting" in names


def test_voice_available_when_installed():
    assert sp.voice_available(SAY_OUTPUT_WITH_YUNA) is True


def test_enhanced_variant_counts_as_available():
    assert sp.voice_available(SAY_OUTPUT_ENHANCED_ONLY) is True


def test_voice_missing():
    assert sp.voice_available(SAY_OUTPUT_WITHOUT_YUNA) is False  # YunaX ≠ Yuna
    assert sp.voice_available("") is False


# -- speak-state machine ---------------------------------------------------


def test_no_stacking_while_speaking():
    session = sp.SpeechSession()
    assert session.begin(player_playing=True) is True
    assert session.begin(player_playing=True) is False  # rapid double-click
    assert session.begin(player_playing=False) is False


def test_resume_only_if_we_paused():
    session = sp.SpeechSession()
    session.begin(player_playing=True)  # we pause Spotify for this speech
    assert session.should_resume is True
    assert session.finish() is True  # so playback resumes


def test_speak_while_paused_stays_paused():
    session = sp.SpeechSession()
    session.begin(player_playing=False)  # user had already paused
    assert session.should_resume is False
    assert session.finish() is False  # stay paused


def test_session_reusable_after_finish():
    session = sp.SpeechSession()
    session.begin(player_playing=True)
    session.finish()
    assert session.speaking is False
    assert session.begin(player_playing=False) is True
    assert session.finish() is False  # new session's own decision, not stale


# -- button visibility gating ----------------------------------------------


def test_button_visible_requires_all_conditions():
    assert sp.button_visible(True, "안녕하세요", True, True) is True


def test_button_hidden_when_any_condition_fails():
    korean = "안녕하세요"
    assert sp.button_visible(False, korean, True, True) is False   # not synced
    assert sp.button_visible(True, "english line", True, True) is False  # no hangul
    assert sp.button_visible(True, "", True, True) is False        # no current line
    assert sp.button_visible(True, korean, False, True) is False   # toggled off
    assert sp.button_visible(True, korean, True, False) is False   # voice missing
