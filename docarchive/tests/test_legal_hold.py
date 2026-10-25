import frappe

from docarchive import legal_hold, run, store
from docarchive.tests.factories import make_document_type, make_manager, make_outsider
from docarchive.tests.hold_case import DOCUMENT, HoldTestCase


class TestSetLegalHold(HoldTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_sets_hold_on_every_recorded_version_with_the_archive_key(self):
		doc = self.archived()
		result = legal_hold.set_legal_hold([doc.name], "Klage Müller GmbH")
		self.assertEqual(result[doc.name]["status"], "held")
		doc.reload()
		self.assertEqual((doc.legal_hold, doc.legal_hold_reason), (1, "Klage Müller GmbH"))
		self.assertEqual({copy.legal_hold for copy in doc.archive_copies}, {"On"})
		self.assertEqual(
			sorted((b, k, v) for b, k, v, status, by in self.holds()),
			self.recorded(doc),
		)
		self.assertEqual({(status, by) for *_rest, status, by in self.holds()}, {("ON", "archive")})
		for copy in doc.archive_copies:
			target = next(t for t in store.targets() if t.name == copy.target_name)
			for key, version in run.copy_objects(copy, doc):
				self.assertEqual(store.hold_status(target, key, version), "ON")
		[entry] = doc.legal_hold_log
		self.assertEqual(
			(entry.action, entry.user, entry.reason), ("Set", "Administrator", "Klage Müller GmbH")
		)

	def test_hold_needs_reason_archive_and_write_permission(self):
		doc = self.archived()
		with self.assertRaises(frappe.ValidationError):
			legal_hold.set_legal_hold([doc.name], "  ")
		fresh = self.document()
		self.assertEqual(legal_hold.set_legal_hold([fresh.name], "x")[fresh.name]["status"], "refused")
		frappe.set_user(make_outsider("outsider-hold@example.test"))
		with self.assertRaises(frappe.PermissionError):
			legal_hold.set_legal_hold([doc.name], "x")
		frappe.set_user(make_manager("manager-hold@example.test"))
		self.assertEqual(legal_hold.set_legal_hold([doc.name], "x")[doc.name]["status"], "held")

	def test_refused_by_every_target_marks_nothing_as_held(self):
		doc = self.archived()
		for fake in self.fakes.values():
			fake.deny = {("archive", "put_object_legal_hold")}
		result = legal_hold.set_legal_hold([doc.name], "x")[doc.name]
		self.assertEqual(result["status"], "refused")
		self.assertEqual(result["message"], legal_hold.hold_refused())
		doc.reload()
		self.assertEqual(doc.legal_hold, 0)
		self.assertEqual({copy.legal_hold for copy in doc.archive_copies}, {"Off"})
		self.assertEqual(len(doc.legal_hold_log), 1)

	def test_refused_by_one_target_is_partial(self):
		doc = self.archived()
		self.fake("secondary").deny = {("archive", "put_object_legal_hold")}
		self.assertEqual(legal_hold.set_legal_hold([doc.name], "x")[doc.name]["status"], "partial")
		doc.reload()
		self.assertEqual(doc.legal_hold, 1)
		states = {copy.target_name: copy.legal_hold for copy in doc.archive_copies}
		self.assertEqual(states, {"primary": "On", "secondary": "Off"})

	def test_new_copy_and_extension_sidecar_get_the_hold(self):
		self.type = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		self.configure(["primary"])
		doc = self.archived(contract_end_date="2026-12-31")
		legal_hold.set_legal_hold([doc.name], "x")
		self.configure(["primary", "secondary"])
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Archived")
		doc.reload()
		doc.contract_end_date = "2030-06-30"
		doc.save(ignore_permissions=True)
		self.assertEqual(run.nightly()["extended"], 1)
		doc.reload()
		self.assertEqual({copy.legal_hold for copy in doc.archive_copies}, {"On"})
		held = {(b, k, v) for b, k, v, status, _by in self.holds() if status == "ON"}
		self.assertEqual(set(self.recorded(doc)), held)
		self.assertEqual(len(self.recorded(doc)), 6)

	def test_held_document_cannot_be_trashed(self):
		doc = self.archived()
		legal_hold.set_legal_hold([doc.name], "x")
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(DOCUMENT, doc.name, ignore_permissions=True)


class TestReleaseLegalHold(HoldTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def held(self):
		doc = self.archived()
		legal_hold.set_legal_hold([doc.name], "Klage")
		return frappe.get_doc(DOCUMENT, doc.name)

	def test_release_uses_the_admin_key_and_logs(self):
		doc = self.held()
		result = legal_hold.release_legal_hold([doc.name], "Verfahren beendet", confirm=1)
		self.assertEqual(result[doc.name]["status"], "released")
		doc.reload()
		self.assertEqual((doc.legal_hold, doc.legal_hold_reason), (0, None))
		self.assertEqual({copy.legal_hold for copy in doc.archive_copies}, {"Off"})
		off = [(b, k, v, by) for b, k, v, status, by in self.holds() if status == "OFF"]
		self.assertEqual(sorted(row[:3] for row in off), self.recorded(doc))
		self.assertEqual({row[3] for row in off}, {"admin"})
		self.assertEqual([entry.action for entry in doc.legal_hold_log], ["Set", "Release"])
		self.assertEqual(doc.legal_hold_log[1].reason, "Verfahren beendet")

	def test_release_needs_role_reason_and_confirmation(self):
		doc = self.held()
		for reason, confirm in (("", 1), ("x", 0)):
			with self.subTest(reason=reason, confirm=confirm), self.assertRaises(frappe.ValidationError):
				legal_hold.release_legal_hold([doc.name], reason, confirm=confirm)
		frappe.set_user(make_manager("manager-release@example.test"))
		with self.assertRaises(frappe.PermissionError):
			legal_hold.release_legal_hold([doc.name], "x", confirm=1)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.get_doc(DOCUMENT, doc.name).legal_hold, 1)

	def test_release_without_admin_key_is_unavailable(self):
		doc = self.held()
		for admin in ((), ("primary",)):
			with self.subTest(admin=admin):
				self.configure(["primary", "secondary"], admin=admin)
				self.assertEqual(legal_hold.admin_access(), bool(admin))
				with self.assertRaises(frappe.ValidationError) as caught:
					legal_hold.release_legal_hold([doc.name], "x", confirm=1)
				self.assertIn("is not configured", str(caught.exception))
		self.assertEqual([h for h in self.holds() if h[3] == "OFF"], [])
		self.assertEqual(frappe.get_doc(DOCUMENT, doc.name).legal_hold, 1)

	def test_failed_release_keeps_the_hold(self):
		doc = self.held()
		self.fake("secondary").deny = {("admin", "put_object_legal_hold")}
		result = legal_hold.release_legal_hold([doc.name], "x", confirm=1)
		self.assertEqual(result[doc.name]["status"], "partial")
		doc.reload()
		self.assertEqual(doc.legal_hold, 1)
		states = {copy.target_name: copy.legal_hold for copy in doc.archive_copies}
		self.assertEqual(states, {"primary": "Off", "secondary": "Failed"})

	def test_admin_access_needs_the_role(self):
		self.assertTrue(legal_hold.admin_access())
		frappe.set_user(make_manager("manager-access@example.test"))
		self.assertFalse(legal_hold.admin_access())
