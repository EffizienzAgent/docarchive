import frappe
from frappe.tests import IntegrationTestCase

from docarchive import export_package, settings, setup
from docarchive.patches import apply_settings_defaults

SETTINGS = "Archive Settings"


def stored(field):
	rows = frappe.db.sql("select value from tabSingles where doctype=%s and field=%s", (SETTINGS, field))
	return rows[0][0] if rows else "missing"


def store(field, value):
	frappe.db.sql("delete from tabSingles where doctype=%s and field=%s", (SETTINGS, field))
	if value != "missing":
		frappe.db.sql(
			"insert into tabSingles (doctype, field, value) values (%s, %s, %s)", (SETTINGS, field, value)
		)
	frappe.clear_document_cache(SETTINGS)


class TestSettingsDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.clear_document_cache(SETTINGS)

	def test_unset_values_get_their_default(self):
		for field, default in settings.DEFAULTS.items():
			for value in ("missing", None, ""):
				store(field, value)
				apply_settings_defaults.execute()
				self.assertEqual(stored(field), str(default), (field, value))

	def test_explicit_values_are_kept(self):
		for field in settings.DEFAULTS:
			for value in ("0", "5"):
				store(field, value)
				apply_settings_defaults.execute()
				self.assertEqual(stored(field), value, field)

	def test_zero_written_by_older_versions_is_repaired(self):
		from docarchive.patches import repair_zero_settings

		for field in repair_zero_settings.FIELDS:
			for value, expected in (("0", str(settings.DEFAULTS[field])), ("5", "5")):
				store(field, value)
				repair_zero_settings.execute()
				self.assertEqual(stored(field), expected, (field, value))

	def test_defaults_match_the_doctype(self):
		meta = frappe.get_meta(SETTINGS)
		for field, default in settings.DEFAULTS.items():
			self.assertEqual(meta.get_field(field).default, str(default), field)

	def test_fresh_install_gets_the_defaults(self):
		for field in settings.DEFAULTS:
			store(field, "missing")
		setup.apply_defaults()
		frappe.clear_document_cache(SETTINGS)
		doc = frappe.get_single(SETTINGS)
		doc.save(ignore_permissions=True)
		values = frappe.db.get_values_from_single(list(settings.DEFAULTS), None, SETTINGS, as_dict=True)[0]
		self.assertEqual(
			{field: int(values[field]) for field in settings.DEFAULTS},
			{
				"export_show_credit": 1,
				"overdue_hours": 48,
				"export_expiry_days": 7,
				"mail_write_timeout": 30,
				"follow_threads": 1,
			},
		)

	def test_save_fills_empty_values_and_keeps_zero(self):
		doc = frappe.get_single(SETTINGS)
		doc.overdue_hours, doc.export_expiry_days, doc.mail_write_timeout = None, 0, None
		doc.save(ignore_permissions=True)
		self.assertEqual((doc.overdue_hours, doc.export_expiry_days, doc.mail_write_timeout), (48, 0, 30))

	def test_unset_credit_reads_as_on(self):
		for value in ("missing", None, ""):
			store("export_show_credit", value)
			self.assertTrue(export_package.brand()["credit"], value)
		store("export_show_credit", "0")
		self.assertFalse(export_package.brand()["credit"])


class TestMailOriginalIndexes(IntegrationTestCase):
	def indexes(self):
		return {row[2] for row in frappe.db.sql("show index from `tabMail Original`")}

	def indexed_columns(self):
		return {row[4] for row in frappe.db.sql("show index from `tabMail Original`")}

	def test_audit_pending_is_indexed(self):
		self.assertIn("audit_pending", self.indexed_columns())

	def test_stale_index_is_dropped(self):
		from docarchive.patches import drop_stale_mail_index

		drop_stale_mail_index.execute()
		self.assertNotIn(drop_stale_mail_index.STALE, self.indexes())
