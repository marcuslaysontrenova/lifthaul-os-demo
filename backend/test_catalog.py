"""RGO OS backend — catalog/remaining-entity tests (§23) + maintenance block."""
import unittest
import core, ops, catalog
from core import (create_user, login, actor_for, create_customer, create_booking,
                  ConflictError, ForbiddenError, ValidationError, NotFoundError)
from catalog import (add_contact, add_address, add_equipment, equipment_available, add_vehicle,
                     add_employee, open_work_order, close_work_order, add_inspection,
                     add_supplier_invoice, post_message, customer_thread, staff_thread,
                     set_config, get_config)
from admin import sup_create


class Base(unittest.TestCase):
    def setUp(self):
        self.c = catalog.connect_full(":memory:")
        for e, r in [("admin@r", "admin"), ("ops@r", "operations_manager"),
                     ("mech@r", "mechanic"), ("fin@r", "finance"), ("drv@r", "driver")]:
            create_user(self.c, e, "pw", r, r)
        A = lambda e: actor_for(self.c, login(self.c, e, "pw"))
        self.admin, self.ops, self.mech, self.fin, self.drv = A("admin@r"), A("ops@r"), A("mech@r"), A("fin@r"), A("drv@r")
        self.cid = create_customer(self.c, self.admin, "Acme")


class TestContactsAddresses(Base):
    def test_create(self):
        cn = add_contact(self.c, self.ops, self.cid, "J. Roe", "j@acme.demo", role="CFO")
        ad = add_address(self.c, self.ops, self.cid, "billing", "123 St", "Makati")
        self.assertTrue(cn and ad)


class TestEquipmentMaintenanceBlock(Base):
    def test_wo_makes_equipment_unavailable_and_blocks_reservation(self):
        add_equipment(self.c, self.ops, "CC-250", "250t Crawler", "crane", "250 t")
        self.assertTrue(equipment_available(self.c, "CC-250"))
        b = create_booking(self.c, self.ops, self.cid, "Crane", "load")
        ops.reserve_resource(self.c, self.ops, b, "crane", "CC-250")   # ok while ACTIVE
        # now open a maintenance WO -> equipment MAINTENANCE
        wo = open_work_order(self.c, self.mech, "CC-250", "hydraulics")
        self.assertFalse(equipment_available(self.c, "CC-250"))
        b2 = create_booking(self.c, self.ops, self.cid, "Crane", "load2")
        with self.assertRaises(ConflictError):
            ops.reserve_resource(self.c, self.ops, b2, "crane", "CC-250")   # maintenance block
        close_work_order(self.c, self.mech, wo, cost=48000)
        self.assertTrue(equipment_available(self.c, "CC-250"))            # back in service

    def test_duplicate_equipment_code(self):
        add_equipment(self.c, self.ops, "AT-100", "AT", "crane")
        with self.assertRaises(ConflictError):
            add_equipment(self.c, self.ops, "AT-100", "AT dup", "crane")


class TestVehiclesEmployeesInspections(Base):
    def test_create(self):
        add_vehicle(self.c, self.ops, "ABC-123", "prime mover")
        add_employee(self.c, self.admin, "M. Santos", "driver")
        add_inspection(self.c, self.mech, "CC-250", "annual", "PASS")


class TestSupplierInvoices(Base):
    def test_create_and_missing_supplier(self):
        sup = sup_create(self.c, self.ops, "FuelCo")
        si = add_supplier_invoice(self.c, self.fin, sup, 120000)
        self.assertTrue(si)
        with self.assertRaises(NotFoundError):
            add_supplier_invoice(self.c, self.fin, 999, 1)


class TestBookingMessages(Base):
    def test_internal_notes_hidden_from_customer(self):
        b = create_booking(self.c, self.ops, self.cid, "Crane", "load")
        post_message(self.c, self.ops, b, "customer", "Your quote is ready.")
        post_message(self.c, self.ops, b, "internal", "Client is price-sensitive — hold margin.")
        cust = customer_thread(self.c, b)
        staff = staff_thread(self.c, b)
        self.assertEqual(len(cust), 1)                       # only the customer-visible message
        self.assertEqual(len(staff), 2)                      # staff see both
        self.assertNotIn("price-sensitive", " ".join(m["body"] for m in cust))
        with self.assertRaises(ValidationError):
            post_message(self.c, self.ops, b, "secret", "bad")


class TestSystemConfig(Base):
    def test_set_get(self):
        set_config(self.c, self.admin, "downpayment_default_pct", 35)
        self.assertEqual(get_config(self.c, "downpayment_default_pct"), "35")
        self.assertEqual(get_config(self.c, "missing", "def"), "def")


if __name__ == "__main__":
    unittest.main(verbosity=2)
