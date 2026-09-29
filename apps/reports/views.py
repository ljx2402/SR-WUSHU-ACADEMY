import csv

from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import IsAcademyAdmin

from .reports import REPORTS


class ReportView(APIView):
    """GET /api/reports/<name>/?start=YYYY-MM-DD&end=YYYY-MM-DD[&export=csv]"""

    permission_classes = [IsAcademyAdmin]

    def get(self, request, name):
        builder = REPORTS.get(name)
        if builder is None:
            raise Http404("Unknown report")
        try:
            columns, rows = builder(request.query_params)
        except ValueError as exc:
            return Response({"detail": f"Invalid parameter: {exc}"}, status=400)
        if request.query_params.get("export") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            filename = f"{name}-{timezone.localdate():%Y%m%d}.csv"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response.write("﻿")  # BOM so Excel shows Chinese names correctly
            writer = csv.writer(response)
            writer.writerow(columns)
            writer.writerows(rows)
            return response
        return Response({"report": name, "columns": columns, "count": len(rows),
                         "rows": [dict(zip(columns, row)) for row in rows]})


class ReportIndexView(APIView):
    permission_classes = [IsAcademyAdmin]

    def get(self, request):
        return Response({"reports": sorted(REPORTS)})
