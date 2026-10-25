from datetime import UTC, datetime

import frappe
from frappe.utils import add_to_date, now_datetime

from docarchive import mail, run, store
from docarchive.mail import archiving
from docarchive.tests.factories import (
	fields_target,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_settings,
	party_fields,
	store_mail_inline,
)
from docarchive.tests.fake_s3 import AdminClient, LockedFakeS3
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase

ADMIN_KEYS = {"admin_access_key": "admin-key", "admin_secret_key": "admin-secret"}
PAST = datetime(2020, 1, 2, tzinfo=UTC)


class HoldTestCase(ArchiveRunTestCase):
	def fake_for(self, config):
		fake = self.fakes.setdefault(config["bucket"], LockedFakeS3())
		return AdminClient(fake) if config["access_key"] == "admin-key" else fake

	def configure(self, names, enabled=1, admin=True, **modes):
		frappe.db.delete(DOCUMENT, {"archive_status": ("!=", "Archived")})
		keys = admin if isinstance(admin, (list, tuple)) else (names if admin else ())
		make_settings(
			enabled=enabled,
			default_own_company=self.company,
			targets=[
				fields_target(
					name, lock_mode=modes.get(name, "GOVERNANCE"), **(ADMIN_KEYS if name in keys else {})
				)
				for name in names
			],
		)

	def archived(self, **fields):
		doc = self.document(**fields)
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Archived")
		return frappe.get_doc(DOCUMENT, doc.name)

	def archived_mail(self, raw):
		store_mail_inline(self)
		if not getattr(self, "mail_account", None):
			self.mail_account = make_email_account().name
		make_mail_settings(self.mail_account, mode="Manual")
		name = archiving.archive(mail.capture(make_communication(self.mail_account), raw, "t"))
		frappe.db.set_value(
			DOCUMENT,
			name,
			{**party_fields(self.party), "modified": add_to_date(now_datetime(), hours=-48)},
			update_modified=False,
		)
		self.assertEqual(run.archive_document(name, store.targets()), "Archived")
		return frappe.get_doc(DOCUMENT, name)

	def expire(self, doc):
		frappe.db.set_value(DOCUMENT, doc.name, "retain_until", PAST.replace(tzinfo=None))
		for copy in doc.archive_copies:
			frappe.db.set_value("Archive Copy", copy.name, "retain_until", PAST.replace(tzinfo=None))
		for fake in self.fakes.values():
			for versions in fake.versions.values():
				for version in versions:
					version["ObjectLockRetainUntilDate"] = PAST
		return frappe.get_doc(DOCUMENT, doc.name)

	def holds(self):
		return [
			(bucket, kwargs["Key"], kwargs["VersionId"], kwargs["LegalHold"]["Status"], by)
			for bucket, fake in self.fakes.items()
			for (operation, kwargs), by in zip(fake.calls, fake.callers, strict=True)
			if operation == "put_object_legal_hold"
		]

	def recorded(self, doc):
		return sorted(
			(f"archive-{copy.target_name}", key, version)
			for copy in doc.archive_copies
			for key, version in run.copy_objects(copy, doc)
		)
