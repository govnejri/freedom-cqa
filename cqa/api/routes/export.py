"""Экспорт результатов: CSV в формате сравнения с эталоном или полный JSON (cqa/export/exporter.py)."""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response

from cqa.api.deps import get_con

router = APIRouter(tags=["export"])


@router.get("/export")
async def export(
    group: str | None = None,
    format: str = Query("csv", pattern="^(csv|json)$"),
    final: bool = False,
    con: sqlite3.Connection = Depends(get_con),
):
    from cqa.export.exporter import export_csv, export_json

    name = "cqa-" + (re.sub(r"[^A-Za-z0-9._-]+", "_", group) if group else "all") + ("-final" if final else "")
    if format == "json":
        return JSONResponse(export_json(con, group or None, final),
                            headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    return Response(export_csv(con, group or None, final).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
