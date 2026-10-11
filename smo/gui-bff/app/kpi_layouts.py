"""The signed-in user's saved KPI dashboard layouts (GUI-4.3): `GET /api/me/kpi-layouts`, `PUT` and `DELETE /api/me/kpi-layouts/{name}`.

A layout is what the KPIs page's chart shows: up to `MAX_CHARTED` KPIs (by definition name), a range (1 h, 24 h or 7 d) and optionally a region and a
site cluster. Installed by app/main.py next to the preferences; stored by app/db.py's `KpiLayout`, one row per user and name, at most `MAX_KPI_LAYOUTS`
per user. Every field is checked (a KPI name has the shape RAN NF OAM gives one, since the page puts it into a request path); an unknown field is refused
(422). Any signed-in user may keep layouts, and only their own: there is no username in the path. A layout names KPIs, it does not grant them: the
chart reads each through the usual proxy and the user's own permissions.
"""

import json
import re
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .db import MAX_KPI_LAYOUTS

MAX_CHARTED = 8
LAYOUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$")
KpiName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")]      # ran-nf-oam/app/kpi.py NAME


class KpiLayout(BaseModel):
    """One layout: the KPIs charted (1 to `MAX_CHARTED`, no repeats, in the order shown), the range, and the region and site cluster the chart is
    narrowed to (null: everything the user may read)."""
    model_config = ConfigDict(extra="forbid")

    kpis: list[KpiName] = Field(min_length=1, max_length=MAX_CHARTED)
    range: Literal["1h", "24h", "7d"] = "24h"
    region: str | None = Field(default=None, min_length=1, max_length=100)
    siteCluster: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("kpis")
    @classmethod
    def _no_repeats(cls, kpis: list[str]) -> list[str]:
        """A KPI is charted once."""
        if len(set(kpis)) != len(kpis):
            raise ValueError("a KPI is listed twice")
        return kpis


def install(app: FastAPI, *, current_session, problem) -> None:
    """Add the three routes to `app`; `current_session` is main.py's session dependency (it also checks the CSRF token on a change) and `problem` its
    error-body helper."""

    def bad_name():
        return problem(422, "INVALID_LAYOUT_NAME", "a layout name is 1 to 60 letters, digits, spaces, '.', '_' or '-', starting with a letter or digit")

    @app.get("/api/me/kpi-layouts")
    def my_kpi_layouts(session=Depends(current_session)):
        """The user's layouts by name, each `{name, updatedAt, kpis, range, region, siteCluster}`. A stored layout that no longer validates (a field
        this release changed) is left out rather than failing the list."""
        items = []
        for name, value, updated_at in app.state.db.kpi_layouts(session.user.username):
            try:
                layout = KpiLayout(**json.loads(value))
            except ValueError:
                continue
            items.append({"name": name, "updatedAt": updated_at.isoformat(), **layout.model_dump()})
        return {"max": MAX_KPI_LAYOUTS, "items": items}

    @app.put("/api/me/kpi-layouts/{name}")
    def save_kpi_layout(name: str, body: KpiLayout, session=Depends(current_session)):
        """Save the layout under `name`, replacing one of that name. 422 for a bad name or field; 409 KPI_LAYOUT_LIMIT for a new name when the user
        already has `MAX_KPI_LAYOUTS`."""
        if not LAYOUT_NAME.match(name):
            return bad_name()
        if app.state.db.save_kpi_layout(session.user.username, name, body.model_dump_json()) == "full":
            return problem(409, "KPI_LAYOUT_LIMIT", f"at most {MAX_KPI_LAYOUTS} layouts can be saved: delete one first")
        return {"name": name, **body.model_dump()}

    @app.delete("/api/me/kpi-layouts/{name}", status_code=204)
    def delete_kpi_layout(name: str, session=Depends(current_session)):
        """Delete the user's layout `name`; 204 whether or not it existed."""
        app.state.db.remove_kpi_layout(session.user.username, name)
        return Response(status_code=204)
