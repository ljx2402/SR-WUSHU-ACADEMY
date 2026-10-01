"""Phase 6H: the payroll API as the Payroll Staff Portal and the coach's
"My payslips" use it.

Fixtures (``PayrollTestCase``): Coach A (general RM 80, substitute RM 100) and
Coach B (class B RM 70) each coach one session today; the payroll month is
calculated after it has ended. FINANCE_ADMIN calculates, only SUPER_ADMIN
finalizes; ADMIN has no payroll capability. A coach sees only their own
FINALIZED payslips.
"""

import datetime

from rest_framework.test import APIClient

from apps.payroll.models import PayrollRun, Payslip
from apps.payroll.tests.test_payroll import PayrollTestCase, at

BANK = {"bank_name", "bank_account_no", "epf_no", "socso_no", "ic_number"}


def keys_in(data):
    if isinstance(data, dict):
        return set(data) | {k for v in data.values() for k in keys_in(v)}
    if isinstance(data, list):
        return {k for v in data for k in keys_in(v)}
    return set()


class PayrollStaffTestCase(PayrollTestCase):
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return body.get("results", body)

    def previous_month_run(self):
        """A second, finalized period (last month) with Coach A's own session."""
        last = self.today.replace(day=1) - datetime.timedelta(days=1)
        session = self.class_a.sessions.create(date=last, start_time=datetime.time(17), end_time=datetime.time(19))
        session.coach_slots.create(coach=self.coach_a)
        from apps.payroll.services import calculate_run, finalize_run

        with at(self.after_month):
            run = calculate_run(last.year, last.month, self.finance_user)
            return finalize_run(run, self.super_user)


class PayslipFilterTests(PayrollStaffTestCase):
    def test_run_and_coach_filters_for_payroll_staff(self):
        current = self.calculate()
        previous = self.previous_month_run()
        client = self.api(self.finance_user)
        self.assertEqual(len(self.rows(client.get("/api/payslips/"))), 3)
        rows = self.rows(client.get("/api/payslips/", {"run": current.pk}))
        self.assertEqual({r["coach_name"] for r in rows}, {"Coach A", "Coach B"})
        self.assertEqual({(r["year"], r["month"]) for r in rows}, {(current.year, current.month)})
        rows = self.rows(client.get("/api/payslips/", {"coach": self.coach_a.pk}))
        self.assertEqual({r["month"] for r in rows}, {current.month, previous.month})
        rows = self.rows(client.get("/api/payslips/", {"run": previous.pk, "coach": self.coach_b.pk}))
        self.assertEqual(rows, [])
        for bad in ({"run": "x"}, {"coach": "1;"}):
            self.assertEqual(client.get("/api/payslips/", bad).status_code, 400, bad)

    def test_lines_explain_each_payment_without_bank_details(self):
        from apps.academy.services import assign_substitute

        self.coach_a.bank_account_no = "9988776655"
        self.coach_a.save()
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user, reason="x")
        run = self.calculate()
        body = self.api(self.finance_user).get("/api/payslips/", {"run": run.pk, "coach": self.coach_a.pk}).json()
        payslip = body["results"][0]
        sub = next(line for line in payslip["lines"] if line["kind"] == "SUBSTITUTE_SESSION")
        self.assertEqual((sub["original_coach"], sub["rate"], sub["amount"], sub["rule"]),
                         ("Coach B", "100.00", "100.00", "GENERAL_SUBSTITUTE"))
        self.assertEqual((payslip["gross_pay"], payslip["net_pay"], payslip["run_status"]), ("180.00", "180.00", "READY"))
        self.assertFalse(BANK & keys_in(body))
        self.assertNotIn("9988776655", str(body))


class CoachOwnPayslipTests(PayrollStaffTestCase):
    def test_coach_sees_only_own_finalized_payslips_and_filters_cannot_widen(self):
        run = self.calculate()
        coach = self.api(self.coach_a_user)
        self.assertEqual(self.rows(coach.get("/api/payslips/")), [])  # READY is not visible to coaches
        self.assertEqual(self.rows(coach.get("/api/payslips/", {"run": run.pk})), [])
        run = self.finalize(run)
        mine = Payslip.objects.get(run=run, coach=self.coach_a)
        theirs = Payslip.objects.get(run=run, coach=self.coach_b)
        self.assertEqual([r["id"] for r in self.rows(coach.get("/api/payslips/"))], [mine.pk])
        self.assertEqual(self.rows(coach.get("/api/payslips/", {"coach": self.coach_b.pk})), [])
        self.assertEqual([r["id"] for r in self.rows(coach.get("/api/payslips/", {"run": run.pk}))], [mine.pk])
        self.assertEqual(coach.get(f"/api/payslips/{theirs.pk}/").status_code, 404)
        self.assertEqual(coach.get(f"/api/payslips/{mine.pk}/").json()["net_pay"], "80.00")
        self.assertEqual(coach.get(f"/api/payroll-runs/{run.pk}/").status_code, 403)

    def test_draft_payslip_is_not_found_for_its_coach(self):
        run = self.calculate()
        mine = Payslip.objects.get(run=run, coach=self.coach_a)
        self.assertEqual(self.api(self.coach_a_user).get(f"/api/payslips/{mine.pk}/").status_code, 404)


class PayrollRoleMatrixTests(PayrollStaffTestCase):
    def test_direct_api_per_role(self):
        run = self.calculate()
        payload = {"year": run.year, "month": run.month}
        matrix = {
            # list runs, run detail, payslips of the run, calculate, finalize
            self.super_user: (200, 200, 200, 200, None),
            self.finance_user: (200, 200, 200, 200, 403),
            self.admin_user: (403, 403, 403, 403, 403),
            self.coach_a_user: (403, 403, 200, 403, 403),
            self.parent_1_user: (403, 403, 403, 403, 403),
        }
        for user, expected in matrix.items():
            client = self.api(user)
            with at(self.after_month):
                got = (client.get("/api/payroll-runs/").status_code,
                       client.get(f"/api/payroll-runs/{run.pk}/").status_code,
                       client.get("/api/payslips/", {"run": run.pk}).status_code,
                       client.post("/api/payroll-runs/calculate/", payload).status_code,
                       None if expected[4] is None else client.post(f"/api/payroll-runs/{run.pk}/finalize/").status_code)
            self.assertEqual(got, expected, user.username)
        self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, PayrollRun.Status.READY)
        with at(self.after_month):
            self.assertEqual(self.api(self.super_user).post(f"/api/payroll-runs/{run.pk}/finalize/").status_code, 200)

    def test_finalize_refusals_come_back_as_400_with_the_backend_reason(self):
        from apps.payroll.models import CoachRate

        CoachRate.objects.filter(pk=self.b_class.pk).update(effective_to=self.since)  # Coach B has no rate now
        run = self.calculate()
        self.assertEqual((run.status, run.issue_count), ("DRAFT", 1))
        with at(self.after_month):
            response = self.api(self.super_user).post(f"/api/payroll-runs/{run.pk}/finalize/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("unresolved issue", str(response.json()))
        with at(self.clock):  # the month has not ended yet
            response = self.api(self.finance_user).post("/api/payroll-runs/calculate/", {"year": run.year,
                                                                                         "month": run.month})
            self.assertEqual(response.status_code, 200)
            response = self.api(self.super_user).post(f"/api/payroll-runs/{run.pk}/finalize/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api(self.finance_user).post("/api/payroll-runs/calculate/", {"year": "x"}).status_code,
                         400)

    def test_finalized_runs_and_payslips_cannot_be_changed_through_the_api(self):
        run = self.finalize(self.calculate())
        payslip = Payslip.objects.get(run=run, coach=self.coach_a)
        client = self.api(self.super_user)
        self.assertEqual(client.patch(f"/api/payroll-runs/{run.pk}/", {"status": "DRAFT"}, format="json").status_code,
                         405)
        self.assertEqual(client.delete(f"/api/payroll-runs/{run.pk}/").status_code, 405)
        self.assertEqual(client.put(f"/api/payslips/{payslip.pk}/", {"net_pay": "1"}, format="json").status_code, 405)
        self.assertEqual(client.delete(f"/api/payslips/{payslip.pk}/").status_code, 405)
        with at(self.after_month):
            response = self.api(self.finance_user).post("/api/payroll-runs/calculate/", {"year": run.year,
                                                                                         "month": run.month})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finalized", str(response.json()))
        self.assertEqual(Payslip.objects.get(pk=payslip.pk).net_pay, payslip.net_pay)
