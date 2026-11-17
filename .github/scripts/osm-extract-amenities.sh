#!/usr/bin/env bash
# Match OSM amenities from Geofabrik extracts to snapshot stations (issue #36). Owner: Aarya.
# Usage: .github/scripts/osm-extract-amenities.sh SNAPSHOT_DIR URL [URL ...]
#   URL is an exact Geofabrik .osm.pbf link (from `python -m clickless.data.osm_extract regions`).
#   Writes data/raw/osm/extract_<name>.json per extract; extracts already matched are skipped.
#   Needs osmium-tool. Failures are reported as GitHub annotations naming the extract and step.
set -euo pipefail
snap="$1"; shift
work="${RUNNER_TEMP:-/tmp}/osm-extract"
mkdir -p "$work" data/raw/osm
filters=$(python -m clickless.data.osm_extract filters)
echo "osmium tags-filter expressions: $filters"

fail() {  # fail NAME STEP DETAIL
  echo "::error title=OSM extract ${1}::${2} failed: ${3}"
  exit 1
}

for url in "$@"; do
  name=$(basename "$url"); name="${name%.osm.pbf}"; name="${name%-latest}"
  out="data/raw/osm/extract_${name}.json"
  if [[ -s "$out" ]]; then echo "== $name: already matched, skipped"; continue; fi
  echo "== $name"
  curl -fsSL --show-error --retry 5 --retry-all-errors --retry-delay 20 -o "$work/region.osm.pbf" "$url" \
    || fail "$name" "download" "$url"
  echo "   downloaded $(du -h "$work/region.osm.pbf" | cut -f1)"
  ts=$(osmium fileinfo -g header.option.osmosis_replication_timestamp "$work/region.osm.pbf" 2>/dev/null || true)
  [[ -n "$ts" ]] || ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "   data as of $ts"
  # shellcheck disable=SC2086
  osmium tags-filter "$work/region.osm.pbf" $filters -o "$work/filtered.osm.pbf" --overwrite \
    || fail "$name" "osmium tags-filter" "$url"
  rm -f "$work/region.osm.pbf"
  osmium export "$work/filtered.osm.pbf" -f geojsonseq -u type_id -a type,id,timestamp \
    -o "$work/$name.geojsonseq" --overwrite || fail "$name" "osmium export" "$url"
  rm -f "$work/filtered.osm.pbf"
  python -m clickless.data.osm_extract match --dir "$snap" --geojsonseq "$work/$name.geojsonseq" \
    --name "$name" --source "$url" --timestamp "$ts" || fail "$name" "match" "$url"
  rm -f "$work/$name.geojsonseq"
done
