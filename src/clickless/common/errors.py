"""Internal error markers shared across stages (not a data contract; see contracts/ for those)."""

# Note prefix for an `error` result whose route is not in the offline route cache (#140). The note
# keeps the cache path for debugging and the eval report (evaluation/report.py groups it as
# RouteNotCached); the driver gets a friendly message instead and the CLI does not print it.
NO_CACHED_ROUTE_NOTE = "no cached route"
