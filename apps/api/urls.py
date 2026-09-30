from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import LogoutView, ObtainTokenView
from apps.reports.views import ReportIndexView, ReportView

from . import views

router = DefaultRouter()
router.register("users", views.UserViewSet, basename="user")
router.register("parents", views.ParentViewSet, basename="parent")
router.register("coaches", views.CoachViewSet, basename="coach")
router.register("students", views.StudentViewSet, basename="student")
router.register("enrollments", views.EnrollmentViewSet, basename="enrollment")
router.register("classes", views.TrainingClassViewSet, basename="class")
router.register("sessions", views.TrainingSessionViewSet, basename="session")
router.register("attendance", views.AttendanceRecordViewSet, basename="attendance")
router.register("charges", views.ChargeViewSet, basename="charge")
router.register("families", views.FamilyViewSet, basename="family")
router.register("invoices", views.InvoiceViewSet, basename="invoice")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("refunds", views.RefundViewSet, basename="refund")
router.register("receipts", views.ReceiptViewSet, basename="receipt")
router.register("payment-proofs", views.PaymentProofViewSet, basename="payment-proof")
router.register("competitions", views.CompetitionViewSet, basename="competition")
router.register("competition-events", views.CompetitionEventViewSet, basename="competition-event")
router.register("competition-registrations", views.CompetitionRegistrationViewSet, basename="competition-registration")
router.register("competition-results", views.CompetitionResultViewSet, basename="competition-result")
router.register("payroll-runs", views.PayrollRunViewSet, basename="payroll-run")
router.register("payslips", views.PayslipViewSet, basename="payslip")

urlpatterns = [
    path("auth/token/", ObtainTokenView.as_view(), name="api-token"),
    path("auth/logout/", LogoutView.as_view(), name="api-logout"),
    path("me/", views.MeView.as_view(), name="api-me"),
    path("payment-info/", views.AcademyPaymentInfoView.as_view(), name="payment-info"),
    path("reports/", ReportIndexView.as_view(), name="report-index"),
    path("reports/<str:name>/", ReportView.as_view(), name="report"),
    path("", include(router.urls)),
]
