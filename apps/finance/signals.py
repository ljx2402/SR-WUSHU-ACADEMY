"""Finance events other apps can react to without finance importing them.

charge_settled(charge, actor)    - a charge became fully paid
charge_unsettled(charge, actor)  - a fully paid charge is no longer paid (payment voided)

Receivers run inside the payment transaction, so their effects commit or roll
back together with the payment.
"""

from django.dispatch import Signal

charge_settled = Signal()
charge_unsettled = Signal()
