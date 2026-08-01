"""The seam every other file in this directory stands on.

The window is handed a settings object rather than opening the real one,
and these two say so in the window's own terms: lose the injection and
the whole suite starts editing the developer's preferences.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from sottovoce import settings as preferences


def test_the_window_writes_only_where_it_is_told(make_window, tmp_path):
    """Guard on the seam itself: lose the injection and every test below
    starts editing the real user's preferences."""
    window = make_window()
    assert window._settings.fileName() == str(tmp_path / "sottovoce-test.ini")
    window._set_lyrics_visible(False)
    window._settings.sync()
    assert (tmp_path / "sottovoce-test.ini").exists()


def test_an_injected_settings_file_is_never_migrated_into(make_window):
    """The carry from the LyriSync name runs on the file the window opens
    for itself and on no other. An injected settings object is the
    caller's and arrives complete; copying an older app's preferences into
    it is not the constructor's business — and it is what keeps the whole
    suite off ~/Library/Preferences/com.lyrisync.lyrisync.plist by
    construction rather than by remembering to stub something.

    The legacy door is guarded in conftest, so a regression here would
    also fail as an escape. This says it in the window's own terms.
    """
    window = make_window()
    assert window._settings.value(preferences.MIGRATION_KEY) is None
