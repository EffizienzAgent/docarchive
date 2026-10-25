import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_years, getdate, today
from frappe.utils.data import cast

from docarchive import assignment, content, counterparty, file_names, settings

SOURCE_FIELDS = (
	"source_system",
	"source_account",
	"source_party_type",
	"source_party_id",
	"source_party_name",
	"source_party_email",
	"source_doctype",
	"source_name",
)
LOCKED_FIELDS = (
	"own_company",
	"document_type",
	"document_date",
	"document_number",
	"counterparty_type",
	"counterparty",
	"file",
	*SOURCE_FIELDS,
)
PROTECTED_FIELDS = (
	"content_sha256",
	"archive_status",
	"archive_started_at",
	"archived_at",
	"archive_attempts",
	"archive_error",
	"archive_sha256",
	"archive_size",
	"object_key",
	"meta_key",
	"retention_mode",
	"retain_until",
	"archive_copies",
	"archive_meta",
	"retention_extensions",
	"extension_until",
	"retention_history",
	"extension_meta",
	"legal_hold",
	"legal_hold_reason",
	"legal_hold_log",
	"deleted_at",
	"source_key",
	"replaces",
	*SOURCE_FIELDS,
)
CONTRACT_END = "Contract End Date"
DELETED = "Deleted"
PRIVATE_PREFIX = "/private/files/"


def title_from_file_name(file_name):
	return file_names.date_from_file_name(file_name)[1]


def own_file(doc):
	rows = frappe.get_all(
		"File",
		filters={"file_url": doc.file},
		fields=["file_name", "file_url", "owner", "is_private", "attached_to_doctype", "attached_to_name"],
		order_by="creation desc",
	)
	rows = [row for row in rows if row.file_url == doc.file]
	for row in rows:
		if row.attached_to_doctype == doc.doctype and row.attached_to_name == doc.name:
			return row
	temporary = doc.get("__temporary_name")
	for row in rows:
		if row.owner == frappe.session.user and (
			not row.attached_to_doctype
			or (temporary and row.attached_to_doctype == doc.doctype and row.attached_to_name == temporary)
		):
			return row
	return None


def extension(name):
	stem, dot, ext = (name or "").rpartition(".")
	return ext.lower() if dot and stem else ""


def file_name_of(doc):
	row = own_file(doc)
	return row.file_name if row else doc.file.rsplit("/", 1)[-1]


def default_title(doc):
	if doc.file:
		return title_from_file_name(file_name_of(doc))
	return doc.document_number or doc.document_type


def is_deleting(doc) -> bool:
	return doc.archive_status == DELETED or any(copy.status == DELETED for copy in doc.archive_copies)


def missing_fields(doc) -> list[str]:
	rules = frappe.get_cached_doc("Business Document Type", doc.document_type)
	checks = [
		("File", not doc.file),
		("Counterparty", rules.counterparty_required and not doc.counterparty),
		("Document Number", rules.number_required and not doc.document_number),
		("Contract End Date", rules.contract_end_required and not doc.contract_end_date),
	]
	return [label for label, missing in checks if missing]


class BusinessDocument(Document):
	def _validate_links(self):
		before = self.get_doc_before_save()
		self.restore_protected(before)
		if not (before and before.archive_started_at):
			counterparty.check_read(self, before)
		return super()._validate_links()

	def before_validate(self):
		if self.file and not self.document_date and not self.archive_started_at:
			self.document_date = file_names.date_from_file_name(file_name_of(self))[0]

	def validate(self):
		before = self.get_doc_before_save()
		if before and is_deleting(before) and not self.flags.archive_write:
			frappe.throw(_("Deleted documents cannot be changed."))
		self.restore_protected(before)
		if before and before.archive_started_at:
			self.enforce_lock(before)
		else:
			assignment.assign(self)
			self.resolve_own_company()
			self.validate_file(before)
		counterparty.validate(self)
		self.validate_dates()
		self.validate_contract_end()
		self.set_title(before)

	def on_trash(self):
		if self.archive_started_at:
			frappe.throw(_("Archived documents cannot be deleted."))

	def db_set(self, fieldname, value=None, *args, **kwargs):
		fields = fieldname if isinstance(fieldname, dict) else {fieldname: value}
		if (
			not self.flags.archive_write
			and set(fields) & {*LOCKED_FIELDS, "archive_started_at"}
			and frappe.db.get_value(self.doctype, self.name, "archive_started_at")
		):
			frappe.throw(_("This document is being archived and cannot be changed."))
		return super().db_set(fieldname, value, *args, **kwargs)

	def restore_protected(self, before):
		if self.flags.archive_write:
			return
		for field in PROTECTED_FIELDS:
			self.set(field, before.get(field) if before else self.meta.get_field(field).default)

	def changed(self, before, field):
		fieldtype = self.meta.get_field(field).fieldtype
		return cast(fieldtype, self.get(field)) != cast(fieldtype, before.get(field))

	def enforce_lock(self, before):
		if before.contract_end_date and (
			not self.contract_end_date or getdate(self.contract_end_date) < getdate(before.contract_end_date)
		):
			frappe.throw(_("Once archiving has started, the contract end date can only move later."))
		for field in (*LOCKED_FIELDS, "archive_started_at"):
			if self.changed(before, field):
				frappe.throw(
					_("{0} cannot be changed once archiving has started.").format(
						_(self.meta.get_label(field))
					)
				)

	def resolve_own_company(self):
		self.own_company = settings.own_company_for(self.document_date, self.own_company)
		if not self.own_company:
			frappe.throw(
				_(
					"No own company is set. A System Manager has to create one and set it as default in the Archive Settings."
				)
			)

	def validate_file(self, before):
		if not self.file:
			self.content_sha256 = None
			return
		if before and before.file == self.file:
			return
		if not self.file.startswith(PRIVATE_PREFIX):
			frappe.throw(_("Documents must be uploaded as private files."))
		row = own_file(self)
		if not row:
			frappe.throw(_("Please upload the file directly to this document."))
		if not row.is_private:
			frappe.throw(_("Documents must be uploaded as private files."))
		allowed = settings.allowed_extensions()
		if not {extension(row.file_name), extension(self.file)} <= allowed:
			frappe.throw(_("Only these file types can be filed: {0}").format(", ".join(sorted(allowed))))
		data = content.read_bytes(self.file)
		if data is None:
			frappe.throw(_("File {0} does not exist.").format(self.file))
		self.content_sha256 = content.sha256(data)

	def validate_dates(self):
		limits = settings.date_limits()
		if getdate(self.document_date) > getdate(add_days(today(), limits.future_days)):
			frappe.throw(
				_("The document date cannot be more than {0} days in the future.").format(limits.future_days)
			)
		if self.contract_end_date and getdate(self.contract_end_date) > add_years(
			getdate(self.document_date), limits.contract_years
		):
			frappe.throw(
				_("The contract end date cannot be more than {0} years after the document date.").format(
					limits.contract_years
				)
			)

	def validate_contract_end(self):
		if not self.contract_end_date:
			return
		if (
			frappe.db.get_value("Business Document Type", self.document_type, "retention_from")
			!= CONTRACT_END
		):
			frappe.throw(_("Only contracts have a contract end date."))
		if getdate(self.contract_end_date) < getdate(self.document_date):
			frappe.throw(_("The contract end date cannot be before the document date."))

	def set_title(self, before):
		if not self.title or (before and self.title == default_title(before)):
			self.title = default_title(self)
