import datetime
import unittest

import admin_platform
import core
import db
import founder_dashboard
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class FounderDashboardTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()
        self.founder_id = core.create_user(
            self.conn, "founder@lifthaul.example", "StrongFounder123", "founder", "Founder"
        )
        self.admin_id = core.create_user(
            self.conn, "admin2@lifthaul.example", "StrongAdmin123", "admin", "Administrator"
        )
        self.founder = core.actor_for(
            self.conn, core.login(self.conn, "founder@lifthaul.example", "StrongFounder123")
        )
        self.admin = core.actor_for(
            self.conn, core.login(self.conn, "admin2@lifthaul.example", "StrongAdmin123")
        )

    def tearDown(self):
        self.conn.close()

    def confirm_founder_mfa(self):
        self.conn.execute(
            "INSERT INTO mfa_enrollments(user_id,secret,confirmed,enrolled_at) VALUES(?,?,1,?)",
            (self.founder_id, "JBSWY3DPEHPK3PXP", core.now()),
        )
        self.conn.commit()

    def test_founder_access_is_exclusive_and_requires_confirmed_mfa(self):
        with self.assertRaises(core.ForbiddenError):
            founder_dashboard.financial_summary(self.conn, self.admin)
        with self.assertRaises(core.ForbiddenError):
            founder_dashboard.financial_summary(self.conn, self.founder)
        self.assertTrue(admin_platform.mfa_required_for(
            self.conn, self.conn.execute("SELECT * FROM users WHERE id=?", (self.founder_id,)).fetchone()
        ))
        self.confirm_founder_mfa()
        report = founder_dashboard.financial_summary(self.conn, self.founder)
        self.assertEqual(report["financials"]["gross_booking_value"], 0)

    def test_legacy_wildcard_admin_cannot_create_or_assign_founder(self):
        founder_role = admin_platform.role_by_code(self.conn, "RGO", "founder")
        target_id = core.create_user(
            self.conn, "target@lifthaul.example", "StrongTarget123", "customer", "Target"
        )
        tenant_id = admin_platform.get_tenant(self.conn, "RGO")["id"]
        self.conn.execute("UPDATE users SET tenant_id=? WHERE id=?", (tenant_id, target_id))
        self.conn.commit()
        with self.assertRaises(core.ForbiddenError):
            admin_platform.assign_role(self.conn, target_id, founder_role["id"], actor=self.admin)
        with self.assertRaises(core.ForbiddenError):
            admin_platform.create_user(
                self.conn,
                self.admin,
                "blocked-founder@lifthaul.example",
                "StrongBlocked123",
                "founder",
                "Blocked Founder",
            )

    def test_database_super_platform_admin_can_govern_founder(self):
        super_id = core.create_user(
            self.conn, "super@lifthaul.example", "StrongSuper123", "admin", "Super"
        )
        tenant_id = admin_platform.get_tenant(self.conn, "RGO")["id"]
        self.conn.execute("UPDATE users SET tenant_id=? WHERE id=?", (tenant_id, super_id))
        super_role = admin_platform.role_by_code(self.conn, "RGO", "super_platform_admin")
        admin_platform.assign_role(self.conn, super_id, super_role["id"])
        super_actor = {
            "id": super_id, "role": "admin", "tenant_id": tenant_id, "perms": {"*"}
        }
        created = admin_platform.create_user(
            self.conn,
            super_actor,
            "governed-founder@lifthaul.example",
            "StrongGoverned123",
            "founder",
            "Governed Founder",
        )
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id "
            "WHERE ur.user_id=? AND r.code='founder'",
            (created,),
        ).fetchone())

    def test_financial_report_separates_volume_revenue_and_liabilities(self):
        self.confirm_founder_mfa()
        now = core.now()
        self.conn.execute(
            "INSERT INTO mkt_payment_requirements(booking_id,assignment_id,gross_value,carrier_amount,"
            "platform_fee,payment_fee,tax,protected_amount_required,funded_amount,released_amount,"
            "refunded_amount,status,created_at) VALUES(1,9101,1000,880,100,20,0,1000,1000,400,100,'RELEASED',?)",
            (now,),
        )
        self.conn.execute(
            "INSERT INTO platform_fee_settlements(booking_id,gateway_transaction_id,provider,transfer_timing,"
            "gross_amount,transport_subtotal,fee_rate,fee_amount,currency,status,idempotency_key,refunded_amount,"
            "recovery_amount,created_at,completed_at,updated_at) "
            "VALUES(1,8101,'WISE','AFTER_PROVIDER_CONFIRMATION',1000,900,.1,100,'PHP','COMPLETED',"
            "'founder-fee-1',10,0,?,?,?)",
            (now, now, now),
        )
        self.conn.execute(
            "INSERT INTO billing_evidence(tenant_id,subscription_id,period_start,period_end,plan_version,"
            "base_fee,subtotal,tax,total,payment_status,generated_at) "
            "VALUES(1,1,'2026-10-01','2026-10-31',1,200,200,0,200,'paid',?)",
            (now,),
        )
        self.conn.execute(
            "INSERT INTO mkt_provider_earnings(tenant_id,carrier_id,source_payout_id,gross_amount,platform_fee,"
            "payment_fee,net_amount,paid_amount,status,created_at) VALUES(1,1,7101,800,80,20,700,300,'AVAILABLE',?)",
            (now,),
        )
        self.conn.execute(
            "INSERT INTO mkt_provider_security_deposits(tenant_id,carrier_id,policy_id,required_amount,"
            "funded_amount,applied_amount,refunded_amount,provider_name,status,created_at,updated_at) "
            "VALUES(1,1,1,500,500,50,100,'MOCK','HELD',?,?)",
            (now, now),
        )
        self.conn.execute(
            "INSERT INTO billing_items(tenant_id,account_id,kind,amount,tax,total,status,item_date,created_at) "
            "VALUES(1,1,'CHARGE',800,0,800,'OPEN','2026-10-12',?)",
            (now,),
        )
        self.conn.commit()

        report = founder_dashboard.financial_summary(
            self.conn, self.founder, period="monthly", anchor="2026-10-12T12:00:00+00:00"
        )
        financials = report["financials"]
        balances = report["balances"]
        self.assertEqual(financials["gross_booking_value"], 1000)
        self.assertEqual(financials["transaction_fee_revenue"], 90)
        self.assertEqual(financials["membership_subscription_revenue"], 200)
        self.assertEqual(financials["recognized_platform_revenue"], 290)
        self.assertEqual(financials["cost_of_revenue"], 20)
        self.assertEqual(financials["ebitda"], None)
        self.assertEqual(balances["protected_customer_funds_liability"], 500)
        self.assertEqual(balances["refundable_security_deposit_liability"], 350)
        self.assertEqual(balances["accounts_payable_provider_earnings"], 400)
        self.assertEqual(balances["accounts_receivable"], 800)
        self.assertEqual(balances["cash_and_settlement_reconciliation_difference"], 0)
        self.assertGreaterEqual(
            self.conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='FOUNDER_DASHBOARD_VIEWED'").fetchone()[0],
            1,
        )

    def test_csv_export_preserves_unavailable_accounting_lines(self):
        self.confirm_founder_mfa()
        exported = founder_dashboard.export_csv(self.conn, self.founder)
        self.assertEqual(exported["filename"], "lifthaul-founder-monthly.csv")
        self.assertIn("financials,ebitda,NOT_AVAILABLE", exported["content"])
        self.assertIn("balances,protected_customer_funds_liability,0.0", exported["content"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='FOUNDER_DASHBOARD_EXPORTED'"
            ).fetchone()[0],
            1,
        )

    def test_period_boundaries_are_deterministic(self):
        anchor = datetime.datetime(2026, 10, 12, 8, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(
            founder_dashboard.period_bounds("quarterly", anchor),
            ("quarterly", "2026-10-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
        )
        with self.assertRaises(core.ValidationError):
            founder_dashboard.period_bounds("lifetime", anchor)

    def test_founder_workspace_uses_shared_authenticated_navigation_and_real_routes(self):
        html = (ROOT / "founder.html").read_text(encoding="utf-8")
        script = (ROOT / "founder-workspace.js").read_text(encoding="utf-8")
        server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
        self.assertIn('class="lh-sidebar"', html)
        self.assertIn('href="index.html"', html)
        self.assertIn('autocomplete="one-time-code"', html)
        self.assertNotIn("demo", html.lower())
        self.assertIn("/founder/dashboard/summary", script)
        self.assertIn("/founder/dashboard/export", script)
        self.assertIn('(\"POST\", \"/founder/dashboard/summary\")', server)
        self.assertIn('(\"POST\", \"/founder/dashboard/export\")', server)


if __name__ == "__main__":
    unittest.main(verbosity=2)

