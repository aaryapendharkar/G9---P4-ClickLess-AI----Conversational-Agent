"""Reference implementation of the documented energy model, written from configs/energy.yaml and
the proposal text -- NOT copied from clickless.planning.energy. Owner: Ian.

If this and the planner disagree on a case, that is a finding: fix the documentation first,
then whichever implementation is wrong.
"""

from __future__ import annotations

from clickless.contracts import Vehicle


def leg_soc_drop_pct(distance_km: float, vehicle: Vehicle, penalty: float = 0.0) -> float:
    raise NotImplementedError("TODO(Ian): independent E_req = d * e in SoC %")
