import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from docarchive import content, mail, run
from docarchive.document_types import CORRESPONDENCE, INTERNAL_CORRESPONDENCE
from docarchive.mail import archiving
from docarchive.tests.factories import (
	EML,
	eml,
	make_communication,
	make_email_account,
	make_mail_settings,
	make_manager,
	make_outsider,
	make_own_company,
	make_settings,
	unique,
)

DOCUMENT = "Business Document"


def make_note():
	return frappe.get_doc({"doctype": "Note", "title": unique("party")}).insert(ignore_permissions=True)


def make_contact(email, *links):
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": unique("person"),
			"email_ids": [{"email_id": email, "is_primary": 1}],
			"links": [{"link_doctype": link.doctype, "link_name": link.name} for link in links],
		}
	)
	return contact.insert(ignore_permissions=True)


def address(prefix="client", domain="example.test"):
	return f"{unique(prefix)}@{domain}"


class MailArchivingTestCase(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		make_settings(default_own_company=self.company)
		self.account = make_email_account().name
		self.configure()

	def configure(self, **fields):
		make_mail_settings(self.account, **{"mode": "Manual", "internal_domains": "acme.test", **fields})

	def communication(self, sent_or_received="Received", **fields):
		return make_communication(self.account, sent_or_received, **fields)

	def capture(self, raw=None, comm=None):
		return mail.capture(comm or self.communication(), raw or eml(), "t")

	def document(self, comm):
		name = frappe.db.get_value("Mail Original", {"communication": comm.name}, "business_document")
		return name and frappe.get_doc(DOCUMENT, name)


class TestArchive(MailArchivingTestCase):
	def test_archive_creates_document(self):
		comm = self.communication()
		original = self.capture(EML, comm)
		name = archiving.archive(original)
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual(
			(doc.document_type, doc.correspondence_channel, doc.correspondence_direction, doc.source_doctype),
			("Correspondence", "E-mail", "Incoming", "Communication"),
		)
		self.assertEqual(content.read_bytes(doc.file), EML)
		self.assertEqual(frappe.db.get_value("Mail Original", original, "business_document"), name)
		row = frappe.get_doc("Mail Original", original)
		self.assertEqual(
			(doc.source_name, doc.source_system, doc.source_account), (comm.name, "email", self.account)
		)
		self.assertEqual(
			(doc.title, doc.own_company, doc.source_party_email),
			("Offer", self.company, "client@example.test"),
		)
		self.assertEqual((doc.mail_message_id, doc.thread_key), (row.message_id, row.thread_key))
		self.assertEqual(str(doc.correspondence_at), str(row.mail_at))
		self.assertEqual(doc.content_sha256, row.sha256)
		file_doc = frappe.get_doc("File", {"file_url": doc.file, "attached_to_doctype": DOCUMENT})
		self.assertEqual((file_doc.attached_to_name, file_doc.is_private), (name, 1))

	def test_archive_is_idempotent(self):
		original = self.capture()
		self.assertEqual(archiving.archive(original), archiving.archive(original))

	def test_outgoing_party_is_first_recipient(self):
		raw = eml(From="info@acme.test", To="Bob <bob@example.test>, carol@example.test")
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture(raw, self.communication("Sent"))))
		self.assertEqual(
			(doc.correspondence_direction, doc.source_party_email), ("Outgoing", "bob@example.test")
		)

	def test_mail_without_subject(self):
		raw = eml().replace(b"Subject: Offer\r\n", b"")
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture(raw)))
		self.assertEqual(doc.title, "(no subject)")

	def test_long_subject_is_cut(self):
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture(eml(Subject="x" * 300))))
		self.assertEqual(len(doc.title), 140)

	def test_same_message_id_twice_gives_one_document(self):
		raw = eml()
		first = self.capture(raw)
		second = self.capture(raw)
		name = archiving.archive(first)
		self.assertEqual(archiving.archive(second), name)
		self.assertEqual(frappe.db.get_value("Mail Original", second, "business_document"), name)
		self.assertEqual(
			frappe.db.count(
				DOCUMENT, {"mail_message_id": frappe.db.get_value("Mail Original", first, "message_id")}
			),
			1,
		)

	def test_without_message_id_dedupes_by_hash(self):
		raw = eml().replace(b"Message-ID", b"X-Other")
		name = archiving.archive(self.capture(raw))
		self.assertEqual(archiving.archive(self.capture(raw)), name)
		self.assertNotEqual(archiving.archive(self.capture(eml().replace(b"Message-ID", b"X-Other"))), name)

	def test_account_own_company_is_used(self):
		other = make_own_company("acme-ltd").name
		self.configure(own_company=other)
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture()))
		self.assertEqual(doc.own_company, other)

	def test_internal_mail_gets_internal_type(self):
		raw = eml(From="a@acme.test", To="b@acme.test")
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture(raw)))
		self.assertEqual(doc.document_type, INTERNAL_CORRESPONDENCE)

	def test_archive_without_communication(self):
		original = self.capture()
		frappe.db.set_value("Mail Original", original, "communication", None)
		doc = frappe.get_doc(DOCUMENT, archiving.archive(original))
		self.assertEqual((doc.source_doctype, doc.source_name), ("Communication", None))

	def test_refusal(self):
		self.assertEqual(archiving.refusal(None), "No original was captured for this e-mail.")
		self.assertIsNone(archiving.refusal(self.capture()))


class TestCounterparty(MailArchivingTestCase):
	def archived(self, comm):
		return frappe.get_doc(DOCUMENT, archiving.archive(self.capture(comm=comm)))

	def test_counterparty_from_contact_link(self):
		party = make_note()
		email = address()
		make_contact(email, party)
		doc = self.archived(self.communication(sender=email))
		self.assertEqual((doc.counterparty_type, doc.counterparty), ("Note", party.name))

	def test_contact_without_link_only_when_enabled(self):
		email = address()
		contact = make_contact(email)
		self.assertIsNone(self.archived(self.communication(sender=email)).counterparty)
		self.configure(contacts_as_counterparty=1)
		doc = self.archived(self.communication(sender=email))
		self.assertEqual((doc.counterparty_type, doc.counterparty), ("Contact", contact.name))

	def test_two_parties_stay_unassigned(self):
		first, second = address(), address()
		make_contact(first, make_note())
		make_contact(second, make_note())
		comm = self.communication(sender=first, recipients=f"info@acme.test, {second}")
		self.assertIsNone(self.archived(comm).counterparty)

	def test_internal_contacts_ignored(self):
		self.configure(contacts_as_counterparty=1)
		external, internal = address(), address("staff", "acme.test")
		contact = make_contact(external)
		make_contact(internal)
		doc = self.archived(self.communication(sender=external, recipients=internal))
		self.assertEqual(doc.counterparty, contact.name)

	def test_disallowed_doctype_is_skipped(self):
		email = address()
		make_contact(email, make_note())
		with self.patch_hooks({"docarchive_counterparty_doctypes": ["Contact"]}):
			self.assertIsNone(self.archived(self.communication(sender=email)).counterparty)

	def test_link_added_later_is_taken_by_reassign(self):
		comm = self.communication(sender=address())
		doc = self.archived(comm)
		self.assertIsNone(doc.counterparty)
		party = make_note()
		comm.reload().add_link("Note", party.name, autosave=True)
		run.reassign()
		self.assertEqual(frappe.db.get_value(DOCUMENT, doc.name, "counterparty"), party.name)

	def test_other_documents_untouched(self):
		doc = frappe._dict(source_doctype="User", source_name="Administrator", counterparty=None)
		archiving.assign(doc)
		self.assertIsNone(doc.counterparty)


class TestAutomaticMode(MailArchivingTestCase):
	def test_automatic_mode_archives_on_capture(self):
		self.configure(mode="Automatic")
		comm = self.communication()
		self.capture(comm=comm)
		self.assertEqual(self.document(comm).document_type, CORRESPONDENCE)

	def test_automatic_mode_skips_automated_and_internal(self):
		self.configure(mode="Automatic")
		bulk, internal = self.communication(), self.communication()
		self.capture(eml(Precedence="bulk"), bulk)
		self.capture(eml(From="a@acme.test", To="b@acme.test"), internal)
		self.assertFalse(self.document(bulk) or self.document(internal))
		self.configure(mode="Automatic", archive_internal=1)
		comm = self.communication()
		self.capture(eml(From="a@acme.test", To="b@acme.test"), comm)
		self.assertEqual(self.document(comm).document_type, INTERNAL_CORRESPONDENCE)

	def test_manual_mode_does_not_archive_on_capture(self):
		comm = self.communication()
		self.capture(comm=comm)
		self.assertFalse(self.document(comm))

	def test_auto_archive_is_idempotent(self):
		self.configure(mode="Automatic")
		original = self.capture()
		name = frappe.db.get_value("Mail Original", original, "business_document")
		self.assertEqual(archiving.auto_archive(original), name)

	def test_failing_auto_archive_keeps_capture(self):
		self.configure(mode="Automatic")
		make_settings(default_own_company=None)
		comm = self.communication()
		original = self.capture(comm=comm)
		self.assertTrue(original)
		self.assertFalse(self.document(comm))


class TestDeskApi(MailArchivingTestCase):
	def test_status(self):
		comm = self.communication()
		self.assertEqual(
			archiving.status(comm.name),
			{
				"original": False,
				"business_document": None,
				"automated": False,
				"internal": False,
				"reason": "No original was captured for this e-mail.",
			},
		)
		name = archiving.archive(self.capture(eml(Precedence="bulk"), comm))
		self.assertEqual(
			archiving.status(comm.name),
			{
				"original": True,
				"business_document": name,
				"automated": True,
				"internal": False,
				"reason": None,
			},
		)

	def test_status_needs_read_permission(self):
		comm = self.communication()
		frappe.set_user(make_outsider(address("outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			archiving.status(comm.name)

	def test_archive_communications(self):
		comm = self.communication()
		self.capture(comm=comm)
		result = archiving.archive_communications(frappe.as_json([comm.name]))
		self.assertEqual(result, {"archived": {comm.name: self.document(comm).name}, "refused": {}})

	def test_archive_communications_refuses_without_original(self):
		comm_without_original = self.communication()
		result = archiving.archive_communications([comm_without_original.name])
		self.assertEqual(
			result["refused"][comm_without_original.name], "No original was captured for this e-mail."
		)

	def test_archive_communications_refuses_failed_archive(self):
		comm = self.communication()
		self.capture(comm=comm)
		make_settings(default_own_company=None)
		result = archiving.archive_communications([comm.name])
		self.assertIn(comm.name, result["refused"])
		self.assertFalse(frappe.db.exists(DOCUMENT, {"source_name": comm.name}))

	def test_refused_archive_leaves_no_message(self):
		comm = self.communication()
		self.capture(comm=comm)
		make_settings(default_own_company=None)
		frappe.clear_messages()
		archiving.archive_communications([comm.name])
		self.assertEqual(frappe.local.message_log, [])

	def test_archive_communications_needs_create_permission(self):
		comm = self.communication()
		self.capture(comm=comm)
		frappe.set_user(make_outsider(address("outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			archiving.archive_communications([comm.name])


class TestDiscard(MailArchivingTestCase):
	def archived(self):
		original = self.capture()
		name = archiving.archive(original)
		file_doc = frappe.db.get_value("File", {"attached_to_doctype": DOCUMENT, "attached_to_name": name})
		return name, original, file_doc

	def as_manager(self):
		frappe.set_user(make_manager(address("manager")))
		self.addCleanup(frappe.set_user, "Administrator")

	def test_discard_removes_document_and_original(self):
		name, original, file_doc = self.archived()
		self.as_manager()
		self.assertEqual(archiving.discard(frappe.as_json([name])), {"discarded": [name], "refused": {}})
		self.assertFalse(frappe.db.exists(DOCUMENT, name))
		self.assertFalse(frappe.db.exists("Mail Original", original))
		self.assertFalse(frappe.db.exists("File", file_doc))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": original}))

	def test_discard_refuses_archived(self):
		name, original, _ = self.archived()
		frappe.db.set_value(DOCUMENT, name, "archive_started_at", now_datetime())
		result = archiving.discard([name])
		self.assertEqual(result["discarded"], [])
		self.assertEqual(result["refused"][name], "Archived documents cannot be discarded.")
		self.assertTrue(frappe.db.exists("Mail Original", original))

	def test_discard_refuses_documents_marked_for_legal_hold(self):
		name, original, _ = self.archived()
		frappe.db.set_value(DOCUMENT, name, "hold_on_archive", 1)
		result = archiving.discard([name])
		self.assertEqual(result["refused"][name], "Documents marked for legal hold cannot be discarded.")
		self.assertTrue(frappe.db.exists("Mail Original", original))

	def test_discard_refuses_other_documents(self):
		from docarchive.tests.factories import make_business_document

		doc = make_business_document()
		result = archiving.discard([doc.name])
		self.assertEqual(result["refused"][doc.name], "Only e-mail documents can be discarded.")

	def test_discard_needs_write_permission(self):
		name, _, _ = self.archived()
		frappe.set_user(make_outsider(address("outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			archiving.discard([name])

	def test_remove_document_refuses_archived(self):
		name, _, _ = self.archived()
		frappe.db.set_value(DOCUMENT, name, "archive_started_at", now_datetime())
		with self.assertRaises(frappe.ValidationError):
			archiving.remove_document(name)


class TestCommunicationDelete(MailArchivingTestCase):
	def test_deleting_communication_keeps_archived_mail(self):
		for started in (False, True):
			with self.subTest(started=started):
				comm = self.communication()
				raw = eml()
				original = self.capture(raw, comm)
				name = archiving.archive(original)
				if started:
					frappe.db.set_value(DOCUMENT, name, "archive_started_at", now_datetime())
				doc = frappe.get_doc(DOCUMENT, name)
				self.assertEqual((doc.source_doctype, doc.source_name), ("Communication", comm.name))
				frappe.delete_doc("Communication", comm.name)
				self.assertFalse(frappe.db.exists("Communication", comm.name))
				self.assertEqual(frappe.db.get_value(DOCUMENT, name, "source_name"), comm.name)
				self.assertEqual(content.read_bytes(doc.file), raw)
				self.assertTrue(frappe.db.exists("File", {"attached_to_name": name}))
				self.assertEqual(frappe.db.get_value("Mail Original", original, "communication"), None)

	def test_counterparty_still_protected(self):
		party = make_note()
		comm = self.communication()
		comm.add_link("Note", party.name, autosave=True)
		doc = frappe.get_doc(DOCUMENT, archiving.archive(self.capture(comm=comm)))
		self.assertEqual(doc.counterparty, party.name)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Note", party.name)
