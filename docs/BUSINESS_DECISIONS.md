# Finalized business decisions

Approved rules that later phases must follow. Items marked *(later phase)* are **not implemented
yet**; the current code may still behave differently until that phase lands.

1. **Invoice grouping** *(P1)*: one family receives one invoice covering all of their children,
   e.g. `INV-2026-000123` with lines for Student A RM220, Student B RM280, Student C RM50 =
   RM550. Invoices are academy billing documents associated with the students. There is **no
   "Bill To Parent"** field and **no billing-contact** requirement or fallback logic.
2. **Receipts** are issued for the student(s)/charges they pay, not "billed to a parent".
3. **Payment collection**: `ADMIN` may record payments (cash, bank transfer, other approved
   methods); depositing cash to the academy account afterwards is an operational process.
   `FINANCE_ADMIN` has full finance capabilities; `SUPER_ADMIN` has everything. *(Implemented in
   Phase 0 RBAC.)*
4. **Student accounts**: `STUDENT` is a real role for older (Elite/senior) students, linked
   one-to-one to a Student, limited to their own information. *(Foundation in Phase 0; student UI
   later.)*
5. **Attendance** *(P5)*: coaches may edit attendance until **48 hours after the session ends**.
   `UNMARKED` is neither Present nor Absent in the percentage and is shown separately.
6. **Competition payment** *(P7)*: the fee is paid **at registration**: registration submitted →
   fee generated → payment required immediately → paid → `CONFIRMED`. (Not "approve first, charge
   later".)
7. **Competition refunds** *(P7/P1)*: paid registrations are generally **non-refundable**. `ADMIN`
   or `FINANCE_ADMIN` may authorize an exceptional refund with a reason; it is audited, keeps the
   original receipt and follows proper finance records.
8. **Coach payroll** *(P6)*: **all coaches are paid per training session**; there is no monthly
   salary model and no proration. Only completed eligible sessions are paid. Regular and substitute
   rates may differ (e.g. 18 × RM80 + 5 × RM120 = RM2,040).
9. **Payroll approval**: `FINANCE_ADMIN` prepares/calculates; `SUPER_ADMIN` finalizes.
   *(Implemented in Phase 0: `payroll.finalize` is super admin only.)*
10. **Audit masking** *(P10)*: IC numbers (`******1234`), bank account numbers (`******7890`),
    medical details (`[CHANGED]`) and other highly sensitive data must be masked in audit logs.
11. **Stack**: stay on Django + Django REST Framework + PostgreSQL. Front-end / mobile apps come
    later against the API. Hosting provider not decided yet.
