import frappe

from docarchive import settings

FIELDS = ("overdue_hours", "export_expiry_days")


def execute():
	"""Older versions saved these fields as 0 when they were added; 0 turns the overdue check off."""
	for field in FIELDS:
		if settings.stored(field) == "0":
			frappe.db.set_single_value(
				settings.SETTINGS, field, settings.DEFAULTS[field], update_modified=False
			)
	frappe.clear_document_cache(settings.SETTINGS)
