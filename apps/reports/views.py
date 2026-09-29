import csv

from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from apps.accounts.capabilities import Cap, can

from .reports import REPORTS

REPORT_CAPABILITIES = {
    "students": Cap.REPORTS_STUDENTS,
    "attendance": Cap.REPORTS_ATTENDANCE,
    "competitions": Cap.REPORTS_COMPETITIONS,
    "results": Cap.REPORTS_COMPETITIONS,
    "fees": Cap.REPORTS_FINANCE,
    "payments": Cap.REPORTS_FINANCE,
    "receipts": Cap.REPORTS_FINANCE,
    "payroll": Cap.REPORTS_PAYROLL,
}
assert set(REPORT_CAPABILITIES) == set(REPORTS), "every report needs a capability"


class ReportView(APIView):
    """GET /api/reports/<name>/?start=YYYY-MM-DD&end=YYYY-MM-DD[&export=csv]

    Each report is gated by its own capability (see REPORT_CAPABILITIES)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        builder = REPORTS.get(name)
        if builder is None:
            raise Http404("Unknown report")
        if not can(request.user, REPORT_CAPABILITIES[name]):
            raise PermissionDenied()
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
    """Lists the reports the caller may run."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        allowed = sorted(name for name, cap in REPORT_CAPABILITIES.items() if can(request.user, cap))
        if not allowed:
            raise PermissionDenied()
        return Response({"reports": allowed})
