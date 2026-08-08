"""Terminology guard (Priority 7). Customer-facing wording must be 'Protected Payment' — the word
'escrow' must not appear in the customer/operator UI until counsel authorizes it. Internal
architecture docs may say 'escrow-ready' (clearly architectural, not legal status)."""
import os
import re
import unittest

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
# Customer/operator-facing surfaces that must never claim "escrow".
UI_FILES = [os.path.join(ROOT, "index.html")]

# Only 'escrow-ready' (architectural) is tolerated, and only in docs — never in the UI.
_ESCROW = re.compile(r"escrow", re.IGNORECASE)


class TerminologyGuardTests(unittest.TestCase):
    def test_no_escrow_in_customer_facing_ui(self):
        offenders = []
        for path in UI_FILES:
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if _ESCROW.search(line):
                        offenders.append(f"{os.path.basename(path)}:{n}: {line.strip()[:100]}")
        self.assertEqual(offenders, [], "customer-facing 'escrow' wording is not authorized:\n" + "\n".join(offenders))

    def test_protected_payment_terminology_present(self):
        with open(os.path.join(ROOT, "backend", "protected_payment.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"Protected Payment"', src)
        # the domain must explicitly NOT self-declare legal escrow authorized
        self.assertIn("NOT_YET_AUTHORIZED", src)


if __name__ == "__main__":
    unittest.main()
