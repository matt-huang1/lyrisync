"""Open at Login: what a status means, and who is allowed to be asked.

Registration itself is manual-verify — it changes the real user's login
items and cannot be undone by a test — so the native calls are blocked
suite-wide (see conftest) and what is tested here is everything around
them: the mapping from SMAppServiceStatus to a menu entry, the bundle
gating, and that the guarded paths answer honestly instead of raising.

Nothing here is macOS-only. On the Linux runner _main_app_service returns
None by platform check alone, which is the same branch these assert.
"""

import sys

import pytest

from sottovoce import login_item
from sottovoce.login_item import LoginItemStatus as Status


# -- what a status means --------------------------------------------------


def test_only_enabled_ticks_the_box():
    assert login_item.is_enabled(Status.ENABLED) is True
    for status in (
        Status.NOT_REGISTERED,
        Status.NOT_FOUND,
        Status.UNSUPPORTED,
        Status.REQUIRES_APPROVAL,
    ):
        assert login_item.is_enabled(status) is False


def test_awaiting_approval_is_not_enabled():
    """The case worth being strict about. The registration exists, but
    macOS will not act on it until the user approves it in System
    Settings, so a tick would promise a launch that will not happen."""
    assert login_item.is_enabled(Status.REQUIRES_APPROVAL) is False
    assert login_item.label_for(Status.REQUIRES_APPROVAL) != login_item.MENU_LABEL
    assert "System Settings" in login_item.label_for(Status.REQUIRES_APPROVAL)


def test_every_other_status_uses_the_plain_label():
    for status in (Status.ENABLED, Status.NOT_REGISTERED, Status.NOT_FOUND):
        assert login_item.label_for(status) == login_item.MENU_LABEL


def test_raw_statuses_map_to_the_documented_values():
    """The integers are Apple's, and getting them wrong would silently
    invert the entry."""
    assert login_item.from_raw(0) is Status.NOT_REGISTERED
    assert login_item.from_raw(1) is Status.ENABLED
    assert login_item.from_raw(2) is Status.REQUIRES_APPROVAL
    assert login_item.from_raw(3) is Status.NOT_FOUND


def test_an_unknown_status_is_treated_as_not_found():
    """A status this build has never heard of must not be read as working:
    unknown means unticked, never a tick taken on trust."""
    for raw in (4, 99, -1):
        assert login_item.from_raw(raw) is Status.NOT_FOUND
        assert login_item.is_enabled(login_item.from_raw(raw)) is False


# -- who may be asked -----------------------------------------------------


def test_a_source_checkout_is_not_a_bundle():
    assert login_item.is_bundled(False, "/Users/x/proj/.venv/bin/sottovoce") is False
    assert login_item.is_bundled(False, "/usr/bin/python3") is False


def test_a_frozen_app_bundle_is():
    assert login_item.is_bundled(
        True, "/Applications/SottoVoce.app/Contents/MacOS/SottoVoce"
    ) is True


def test_frozen_alone_is_not_enough():
    """A PyInstaller one-file build sitting in a folder is frozen but has
    no bundle for macOS to register."""
    assert login_item.is_bundled(True, "/Users/x/Downloads/SottoVoce") is False


def test_a_bundle_path_alone_is_not_enough():
    """A script that happens to live inside someone's bundle is not this
    app running as that bundle."""
    assert login_item.is_bundled(
        False, "/Applications/Other.app/Contents/MacOS/script.py"
    ) is False


def test_the_entry_is_offered_only_to_a_supported_bundle():
    assert login_item.offered(bundled=True, status=Status.NOT_REGISTERED) is True
    assert login_item.offered(bundled=True, status=Status.ENABLED) is True
    assert login_item.offered(bundled=False, status=Status.NOT_REGISTERED) is False
    # A bundle that has never been registered reports NOT_FOUND, which is
    # the state every fresh install starts in — offering the switch is the
    # entire point there.
    assert login_item.offered(bundled=True, status=Status.NOT_FOUND) is True
    # macOS 12 or older: SMAppService does not exist, so nothing here can
    # answer for itself and the entry is not offered at all.
    assert login_item.offered(bundled=True, status=Status.UNSUPPORTED) is False


def test_this_test_run_is_not_a_bundle():
    """Sanity on the real process: the suite runs from a checkout, so the
    gating above is the branch every Qt test takes."""
    assert login_item.running_bundled() is False


# -- the guarded native paths ---------------------------------------------


def test_status_without_a_service_is_unsupported(monkeypatch):
    monkeypatch.setattr(login_item, "_main_app_service", lambda: None)
    assert login_item.status() is Status.UNSUPPORTED


def test_setting_it_without_a_service_reports_failure(monkeypatch):
    """Never a silent no-op: the caller has to be able to tell that
    nothing happened, so the menu does not tick."""
    monkeypatch.setattr(login_item, "_main_app_service", lambda: None)
    assert login_item.set_enabled(True) == (False, Status.UNSUPPORTED)
    assert login_item.set_enabled(False) == (False, Status.UNSUPPORTED)


def test_a_service_that_raises_is_survivable(monkeypatch):
    """ServiceManagement is Objective-C across a bridge; an exception here
    must cost the feature and nothing else."""

    class Exploding:
        def status(self):
            raise RuntimeError("bridge said no")

        def registerAndReturnError_(self, _):
            raise RuntimeError("bridge said no")

    monkeypatch.setattr(login_item, "_main_app_service", lambda: Exploding())
    assert login_item.status() is Status.NOT_FOUND
    ok, status = login_item.set_enabled(True)
    assert ok is False


def test_registration_that_needs_approval_is_reported_as_not_done(monkeypatch):
    """The headline case: macOS accepted the registration and returned no
    error, but the app will not start at login until the user approves it.
    set_enabled must not call that success."""

    class NeedsApproval:
        def status(self):
            return 2  # SMAppServiceStatusRequiresApproval

        def registerAndReturnError_(self, _):
            return (True, None)  # no error — and yet

    monkeypatch.setattr(login_item, "_main_app_service", lambda: NeedsApproval())
    ok, status = login_item.set_enabled(True)
    assert ok is False
    assert status is Status.REQUIRES_APPROVAL
    assert login_item.is_enabled(status) is False


def test_a_refused_registration_is_reported(monkeypatch):
    class Refuses:
        def status(self):
            return 0

        def registerAndReturnError_(self, _):
            return (False, "no permission")

    monkeypatch.setattr(login_item, "_main_app_service", lambda: Refuses())
    ok, status = login_item.set_enabled(True)
    assert ok is False
    assert status is Status.NOT_REGISTERED


def test_a_successful_registration_is_reported(monkeypatch):
    class Registers:
        def __init__(self):
            self.calls = []

        def status(self):
            return 1 if self.calls else 0

        def registerAndReturnError_(self, _):
            self.calls.append("register")
            return (True, None)

    service = Registers()
    monkeypatch.setattr(login_item, "_main_app_service", lambda: service)
    assert login_item.set_enabled(True) == (True, Status.ENABLED)
    assert service.calls == ["register"]


def test_unregistering_calls_the_other_side(monkeypatch):
    class Unregisters:
        def __init__(self):
            self.calls = []

        def status(self):
            return 0

        def unregisterAndReturnError_(self, _):
            self.calls.append("unregister")
            return (True, None)

    service = Unregisters()
    monkeypatch.setattr(login_item, "_main_app_service", lambda: service)
    assert login_item.set_enabled(False) == (True, Status.NOT_REGISTERED)
    assert service.calls == ["unregister"]


def test_the_real_service_is_blocked_in_tests(escapes):
    """The guard that keeps a test run from leaving a login item behind on
    whoever ran it. Draining is what lets this test pass."""
    with pytest.raises(RuntimeError, match="test escape"):
        login_item._main_app_service()
    assert any("SMAppService" in escape for escape in escapes.drain())
