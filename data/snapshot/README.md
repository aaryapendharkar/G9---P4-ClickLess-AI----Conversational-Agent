Frozen snapshot goes here (not in git). Download it from the team drive / GitHub release, then run
`python -c "from clickless.data.snapshot import verify_manifest; print(verify_manifest())"` --
an empty list means your copy matches MANIFEST.json.
