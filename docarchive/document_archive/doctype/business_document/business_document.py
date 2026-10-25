import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_years, getdate, today
from frappe.utils.data import cast

from docarchive import assignment, audit, content, counterparty, einvoice, file_names, settings

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
CORRESPONDENCE_FIELDS = ("correspondence_channel", "correspondence_direction", "correspondence_at")
MAIL_FIELDS = ("thread_key", "mail_message_id", "mail_in_reply_to", "mail_references")
LOCKED_FIELDS = (
	"own_company",
	"document_type",
	"document_date",
	"document_number",
	"counterparty_type",
	"counterparty",
	"file",
	*CORRESPONDENCE_FIELDS,
	*MAIL_FIELDS,
	"hold_on_archive",
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
	"attachment_copies",
	"archive_meta",
	"retention_extensions",
	"extension_until",
	"retention_history",
	"extension_meta",
	"einvoice_data",
	"einvoice_needs_review",
	"einvoice_checked",
	"legal_hold",
	"legal_hold_reason",
	"legal_hold_log",
	"deleted_at",
	"source_key",
	"replaces",
	*MAIL_FIELDS,
	*SOURCE_FIELDS,
)
ATTACHMENT_PROTECTED = ("content_sha256", "object_key", "archive_size")
CONTRACT_END = "Contract End Date"
DELETED = "Deleted"
PRIVATE_PREFIX = "/private/files/"


def title_from_file_name(file_name):
	return file_names.date_from_file_name(file_name)[1]


def own_file(doc, file_url=None):
	file_url = file_url or doc.file
	rows = frappe.get_all(
		"File",
		filters={"file_url": file_url},
		fields=[
			"name",
			"file_name",
			"file_url",
			"owner",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
		],
		order_by="creation desc",
	)
	rows = [row for row in rows if row.file_url == file_url]
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


def file_name(doc, file_url):
	row = own_file(doc, file_url)
	return row.file_name if row else file_url.rsplit("/", 1)[-1]


def file_name_of(doc):
	return file_name(doc, doc.file)


def attachment_rows(doc):
	return [(row.name, row.file, row.role, row.title or "") for row in doc.get("attachments") or []]


def default_title(doc):
	from_einvoice = einvoice.title(doc)
	if from_einvoice:
		return from_einvoice
	if doc.file:
		return title_from_file_name(file_name_of(doc))
	return doc.document_number or doc.document_type


def is_deleting(doc) -> bool:
	return doc.archive_status == DELETED or any(copy.status == DELETED for copy in doc.archive_copies)


def missing_fields(doc) -> list[str]:
	rules = frappe.get_cached_doc("Business Document Type", doc.document_type)
	checks = [
		("File", not doc.file),
		("Document Date", not doc.document_date),
		("Own Company", not doc.own_company),
		("Confirmation", doc.get("einvoice_needs_review")),
		("Counterparty", rules.counterparty_required and not doc.counterparty),
		("Document Number", rules.number_required and not doc.document_number),
		("Contract End Date", rules.contract_end_required and not doc.contract_end_date),
		("Channel", rules.is_correspondence and not doc.correspondence_channel),
		("Direction", rules.is_correspondence and not doc.correspondence_direction),
	]
	return [label for label, missing in checks if missing]


class BusinessDocument(Document):
	def _validate_links(self):
		before = self.get_doc_before_save()
		self.restore_protected(before)
		if not (before and before.archive_started_at):
			counterparty.check_read(self, before)
		return super()._validate_links()

	def validate(self):
		before = self.get_doc_before_save()
		if before and is_deleting(before) and not self.flags.archive_write:
			frappe.throw(_("Deleted documents cannot be changed."))
		self.restore_protected(before)
		if before and before.archive_started_at:
			self.enforce_lock(before)
		else:
			self.validate_file(before)
			self.validate_attachments(before)
			einvoice.confirm(self, before)
			einvoice.update(self, before, self.einvoice_files())
			if self.file and not self.document_date:
				self.document_date = file_names.date_from_file_name(file_name_of(self))[0]
			assignment.assign(self)
			self.resolve_own_company()
			einvoice.record_conflicts(self)
		counterparty.validate(self)
		self.validate_dates()
		self.validate_contract_end()
		self.set_title(before)

	def on_update(self):
		self.attach_files()
		self.log_changes()

	def log_changes(self):
		before = self.get_doc_before_save()
		if not before or before.archive_started_at or self.archive_started_at:
			return
		changed = [field for field in LOCKED_FIELDS if self.changed(before, field)]
		if attachment_rows(self) != attachment_rows(before):
			changed.append("attachments")
		if changed:
			audit.note_change(self.doctype, self.name, changed)

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
		self.reset_fields(PROTECTED_FIELDS, before)
		if self.is_mail():
			self.reset_fields(CORRESPONDENCE_FIELDS, before)
		old = {row.name: row for row in (before.get("attachments") if before else [])}
		for row in self.get("attachments") or []:
			prior = old.get(row.name)
			for field in ATTACHMENT_PROTECTED:
				row.set(field, prior.get(field) if prior else None)

	def is_mail(self):
		return bool(
			self.source_system == "email" or self.source_doctype == "Communication" or self.mail_message_id
		)

	def reset_fields(self, fields, before):
		for field in fields:
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
		if attachment_rows(self) != attachment_rows(before):
			frappe.throw(_("Attachments cannot be changed once archiving has started."))

	def resolve_own_company(self):
		if not (self.own_company or self.document_date):
			return
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
		self.content_sha256 = self.checked_sha256(self.file)

	def checked_sha256(self, file_url):
		if not file_url.startswith(PRIVATE_PREFIX):
			frappe.throw(_("Documents must be uploaded as private files."))
		row = own_file(self, file_url)
		if not row:
			frappe.throw(_("Please upload the file directly to this document."))
		if not row.is_private:
			frappe.throw(_("Documents must be uploaded as private files."))
		allowed = settings.allowed_extensions()
		if file_url == self.file and (
			self.source_system == "email" or self.source_doctype == "Communication"
		):
			allowed = {*allowed, "eml"}
		if not {extension(row.file_name), extension(file_url)} <= allowed:
			frappe.throw(_("Only these file types can be filed: {0}").format(", ".join(sorted(allowed))))
		data = content.read_bytes(file_url)
		if data is None:
			frappe.throw(_("File {0} does not exist.").format(file_url))
		return content.sha256(data)

	def validate_attachments(self, before):
		old = {row.name: row for row in (before.get("attachments") if before else [])}
		seen = {self.content_sha256} if self.file else set()
		for row in self.get("attachments") or []:
			if not row.file:
				frappe.throw(_("Attachment {0} has no file.").format(row.idx))
			prior = old.get(row.name)
			if not (prior and prior.file == row.file and row.content_sha256):
				row.content_sha256 = self.checked_sha256(row.file)
			if row.content_sha256 in seen:
				frappe.throw(_("Attachment {0} is already part of this document.").format(row.idx))
			seen.add(row.content_sha256)
			row.title = row.title or title_from_file_name(file_name(self, row.file))

	def einvoice_files(self):
		main = [(file_name(self, self.file), self.file, "main")] if self.file else []
		rows = [(file_name(self, row.file), row.file, f"attachment {row.idx}") for row in self.attachments]
		for name, file_url, source in main + rows:
			if extension(name) in einvoice.EXTENSIONS:
				yield name, content.read_bytes(file_url), source

	def attach_files(self):
		for row in self.get("attachments") or []:
			found = own_file(self, row.file)
			if found and (found.attached_to_doctype, found.attached_to_name) != (self.doctype, self.name):
				frappe.db.set_value(
					"File",
					found.name,
					{
						"attached_to_doctype": self.doctype,
						"attached_to_name": self.name,
						"attached_to_field": "attachments",
					},
				)

	def validate_dates(self):
		if not self.document_date:
			return
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
		if self.document_date and getdate(self.contract_end_date) < getdate(self.document_date):
			frappe.throw(_("The contract end date cannot be before the document date."))

	def set_title(self, before):
		if not self.title or (before and self.title == default_title(before)):
			self.title = default_title(self)
