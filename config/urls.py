from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.finance.views import receipt_print

admin.site.site_header = "SR Wushu Academy"
admin.site.site_title = "SR Wushu Academy"
admin.site.index_title = "Academy administration"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
    path("receipts/<int:pk>/", receipt_print, name="receipt-print"),
]
