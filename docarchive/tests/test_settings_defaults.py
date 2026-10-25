import frappe
from frappe.tests import IntegrationTestCase

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


class TestSettingsDefaults(IntegrationTestCase):
	def tearDown(self):
		frappe.clear_document_cache(SETTINGS)

	def test_unset_values_get_their_default(self):
		for field, default in apply_settings_defaults.DEFAULTS.items():
			for value in ("missing", None, ""):
				store(field, value)
				apply_settings_defaults.execute()
				self.assertEqual(stored(field), str(default), (field, value))

	def test_explicit_values_are_kept(self):
		for field in apply_settings_defaults.DEFAULTS:
			for value in ("0", "5"):
				store(field, value)
				apply_settings_defaults.execute()
				self.assertEqual(stored(field), value, field)

	def test_defaults_match_the_doctype(self):
		meta = frappe.get_meta(SETTINGS)
		for field, default in apply_settings_defaults.DEFAULTS.items():
			self.assertEqual(meta.get_field(field).default, str(default), field)
