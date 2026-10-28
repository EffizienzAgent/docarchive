import frappe
from frappe.tests import IntegrationTestCase

from docarchive import locks


def other(key):
	return frappe.cache.lock(frappe.cache.make_key(f"docarchive:{key}"), timeout=60)


class TestLocks(IntegrationTestCase):
	def tearDown(self):
		locks.release_all()

	def test_held_until_the_transaction_ends(self):
		key = frappe.generate_hash()
		locks.hold(key)
		self.assertFalse(other(key).acquire(blocking=False))
		frappe.db.rollback()
		lock = other(key)
		self.assertTrue(lock.acquire(blocking=False))
		lock.release()

	def test_holding_twice_is_allowed(self):
		key = frappe.generate_hash()
		second = frappe.generate_hash()
		locks.hold(key)
		locks.hold(key, second)
		self.assertFalse(other(second).acquire(blocking=False))
