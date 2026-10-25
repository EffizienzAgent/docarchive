import json

import frappe

from docarchive import counterparty

DOCUMENT = "Business Document"
CHUNK = 500


def execute():
	start = 0
	while True:
		rows = frappe.get_all(
			DOCUMENT,
			filters={"counterparty": ("is", "set"), "counterparty_name": ("is", "not set")},
			fields=["name", "counterparty_type", "counterparty", "archive_meta"],
			order_by="name asc",
			offset=start,
			limit=CHUNK,
		)
		for row in rows:
			name = _archived_name(row) or counterparty.title_of(row.counterparty_type, row.counterparty)
			if name:
				frappe.db.set_value(DOCUMENT, row.name, "counterparty_name", name, update_modified=False)
			else:
				start += 1
		if len(rows) < CHUNK:
			return


def _archived_name(row):
	try:
		meta = json.loads(row.archive_meta) if row.archive_meta else {}
	except ValueError:
		return None
	name = (meta.get("counterparty") or {}).get("name")
	return name[: counterparty.TITLE_LENGTH] if name else None
