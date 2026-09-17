from __future__ import annotations

import check_segment_bleed

# Segments whose highlight paints track they don't run on, waiting on a hand-drawn group
# in the base map's "Path Overrides" layer. Shrink this as they are fixed; the test fails
# either way — on a new bleed, and on a fixed one still listed here.
KNOWN_BLEEDS = set()


def test_segment_highlights_stay_on_their_own_track():
    off_route, no_route, _shared = check_segment_bleed.sweep()
    found = {segment for _, segment, _ in off_route}

    assert not no_route, f"nothing painted joins these stations, check for a gap: {no_route}"
    assert not found - KNOWN_BLEEDS, (
        f"new segment(s) painting off route: {sorted(found - KNOWN_BLEEDS)}. "
        "Run `python check_segment_bleed.py` for pictures."
    )
    assert not KNOWN_BLEEDS - found, f"fixed, so remove from KNOWN_BLEEDS: {sorted(KNOWN_BLEEDS - found)}"
