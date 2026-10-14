"""The release version, and the two places it has to agree with.

`backend/moontv/version.py` stamps `serverVersion` into every `.dat` backup, and
`frontend/src/lib/version.ts` prints `v<CURRENT_VERSION>` on the login page, in
the user menu and in the version panel. Both read the same number, and the root
`VERSION.txt` is the source of truth for it — upstream MoonTV is still 4.2.5,
this rewrite is 5.0.0.
"""

from __future__ import annotations

from pathlib import Path

from conftest import REPO
from moontv.version import CURRENT_VERSION


def test_root_version_txt_is_the_source_of_truth():
    declared = (REPO / "VERSION.txt").read_text().strip()
    assert declared == CURRENT_VERSION, f"VERSION.txt says {declared!r}, version.py says {CURRENT_VERSION!r}"


def test_version_is_a_three_part_number():
    parts = CURRENT_VERSION.split(".")
    assert len(parts) == 3, CURRENT_VERSION
    assert all(part.isdigit() for part in parts), CURRENT_VERSION


def test_frontend_constant_matches_the_backend():
    """The browser and the server must not disagree about which version wrote a backup."""
    source = REPO / "frontend" / "src" / "lib" / "version.ts"
    if not source.is_file():
        raise AssertionError("frontend/src/lib/version.ts is missing")
    text = source.read_text(encoding="utf-8")
    assert f"const CURRENT_VERSION = '{CURRENT_VERSION}'" in text, text.splitlines()[3]


def test_changelog_has_an_entry_for_the_current_version():
    """VersionPanel highlights the entry whose version equals CURRENT_VERSION; without
    one the panel renders an empty 'current version' block."""
    changelog = (REPO / "frontend" / "src" / "lib" / "changelog.ts").read_text(encoding="utf-8")
    assert f'version: "{CURRENT_VERSION}"' in changelog
