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
			fields=["name", "counterparty_type", "counterparty"],
			order_by="name asc",
			offset=start,
			limit=CHUNK,
		)
		for row in rows:
			name = counterparty.title_of(row.counterparty_type, row.counterparty)
			if name:
				frappe.db.set_value(DOCUMENT, row.name, "counterparty_name", name, update_modified=False)
			else:
				start += 1
		if len(rows) < CHUNK:
			return
