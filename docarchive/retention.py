from datetime import UTC, datetime

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

from docarchive import settings

CONTRACT_END = "Contract End Date"


def start_date(doc):
	counts_from = frappe.db.get_value("Business Document Type", doc.document_type, "retention_from")
	if counts_from == CONTRACT_END and doc.contract_end_date:
		return getdate(doc.contract_end_date)
	return getdate(doc.document_date)


def years_for(doc) -> int:
	years = settings.retention_years(doc.own_company, doc.document_type)
	if not years:
		frappe.throw(
			_("There is no retention rule for {0} and {1}.").format(doc.own_company, doc.document_type)
		)
	return years


def retain_until(doc, years=None) -> datetime:
	years = years or years_for(doc)
	return datetime(start_date(doc).year + years + 1, 1, 1, tzinfo=UTC)


def to_db(value):
	return value.astimezone(UTC).replace(tzinfo=None) if value else None


def from_db(value):
	return get_datetime(value).replace(tzinfo=UTC) if value else None
