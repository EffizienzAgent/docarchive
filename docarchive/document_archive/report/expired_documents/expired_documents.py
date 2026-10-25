from frappe import _

from docarchive import expiry


def execute(filters=None):
	columns = [
		{
			"fieldname": "name",
			"label": _("Business Document"),
			"fieldtype": "Link",
			"options": "Business Document",
			"width": 160,
		},
		{"fieldname": "title", "label": _("Title"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "document_type",
			"label": _("Business Document Type"),
			"fieldtype": "Link",
			"options": "Business Document Type",
			"width": 160,
		},
		{"fieldname": "document_date", "label": _("Document Date"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "own_company",
			"label": _("Own Company"),
			"fieldtype": "Link",
			"options": "Own Company",
			"width": 140,
		},
		{
			"fieldname": "counterparty_type",
			"label": _("Counterparty Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"fieldname": "counterparty",
			"label": _("Counterparty"),
			"fieldtype": "Dynamic Link",
			"options": "counterparty_type",
			"width": 160,
		},
		{
			"fieldname": "retain_until",
			"label": _("Retain Until (UTC)"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{"fieldname": "partly_deleted", "label": _("Partly Deleted"), "fieldtype": "Check", "width": 100},
	]
	data = [
		{
			**{column["fieldname"]: doc.get(column["fieldname"]) for column in columns},
			"retain_until": expiry.retention.to_db(expiry.latest_until(doc)),
			"partly_deleted": int(any(copy.status == "Deleted" for copy in doc.archive_copies)),
		}
		for doc in expiry.expired_documents()
	]
	return columns, data
