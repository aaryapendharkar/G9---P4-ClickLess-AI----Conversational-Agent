"""Handoff metrics (proposal §V-C). LEARNING SWAP: Deepti implements, Ian reviews.

  * URL parses; origin/destination/waypoints present and in itinerary order
  * segmented handoffs: every approved stop exactly once; segment k destination == segment k+1 origin
  * zero actions without approval: audit log has no entry for a request the user did not approve
  * clicks / form fields saved vs manual AFDC + Maps planning on 10 scripted trips (manual count)
"""

from __future__ import annotations


def check_urls(urls: list[str], expected_stops: list[tuple[float, float]]) -> dict[str, bool]:
    """Return {"parses": ..., "ordered": ..., "each_stop_once": ..., "boundaries_ok": ...}."""
    raise NotImplementedError("TODO(Deepti, learning swap): parse with urllib.parse and check")
