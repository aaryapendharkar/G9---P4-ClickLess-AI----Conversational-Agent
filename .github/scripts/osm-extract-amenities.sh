#!/usr/bin/env bash
# Match OSM amenities from Geofabrik extracts to snapshot stations (issue #36). Owner: Aarya.
# Usage: .github/scripts/osm-extract-amenities.sh SNAPSHOT_DIR REGION [REGION ...]
#   REGION is a Geofabrik path under north-america/, e.g. us/us-west; end it with '?' to skip it
#   when the file does not exist. Regions already matched (data/raw/osm/extract_NAME.json) are skipped,
#   so a re-run resumes. Needs osmium-tool.
set -euo pipefail
snap="$1"; shift
work="${RUNNER_TEMP:-/tmp}/osm-extract"
mkdir -p "$work" data/raw/osm
filters=$(python -m clickless.data.osm_extract filters)
echo "osmium tags-filter expressions: $filters"
for spec in "$@"; do
  optional=false; region="$spec"
  if [[ "$spec" == *"?" ]]; then optional=true; region="${spec%\?}"; fi
  name=$(basename "$region")
  if [[ -s "data/raw/osm/extract_${name}.json" ]]; then echo "== $name: already matched, skipped"; continue; fi
  url="https://download.geofabrik.de/north-america/${region}-latest.osm.pbf"
  echo "== $name: downloading $url"
  if ! curl -fsSL --show-error --retry 5 --retry-delay 30 -o "$work/region.osm.pbf" "$url"; then
    if $optional; then echo "   not available, skipped"; continue; fi
    exit 1
  fi
  ls -lh "$work/region.osm.pbf"
  ts=$(osmium fileinfo -g header.option.osmosis_replication_timestamp "$work/region.osm.pbf" || true)
  [[ -n "$ts" ]] || ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "   data as of $ts"
  # shellcheck disable=SC2086
  osmium tags-filter "$work/region.osm.pbf" $filters -o "$work/filtered.osm.pbf" --overwrite
  rm -f "$work/region.osm.pbf"
  osmium export "$work/filtered.osm.pbf" -f geojsonseq -u type_id -a type,id,timestamp \
    -o "$work/$name.geojsonseq" --overwrite
  rm -f "$work/filtered.osm.pbf"
  python -m clickless.data.osm_extract match --dir "$snap" --geojsonseq "$work/$name.geojsonseq" \
    --name "$name" --source "$url" --timestamp "$ts"
  rm -f "$work/$name.geojsonseq"
done
