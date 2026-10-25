import frappe

from docarchive.mail import archiving, thread
from docarchive.patches import join_mail_threads
from docarchive.tests.test_mail_archiving import MailArchivingTestCase
from docarchive.tests.test_mail_viewer import message_id, mime, references

DOCUMENT = "Business Document"


class TestThreadRoots(MailArchivingTestCase):
	def mail(self, at, msg):
		comm = self.communication(communication_date=at)
		return self.capture(msg.as_bytes(), comm)

	def split_conversation(self):
		"""Production case: B has In-Reply-To A and no References, C references only B."""
		a_id, b_id, c_id = message_id(), message_id(), message_id()
		a = self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		b = self.mail("2026-03-11 09:00:00", mime(text="b", Message_ID=f"<{b_id}>", In_Reply_To=f"<{a_id}>"))
		c = self.mail(
			"2026-03-12 09:00:00",
			mime(text="c", Message_ID=f"<{c_id}>", In_Reply_To=f"<{b_id}>", References=references(b_id)),
		)
		return a, b, c

	def names(self, items):
		return [item.get("name") or ("gap", item["message_id"]) for item in items]

	def roots(self, *docs):
		return {frappe.db.get_value(DOCUMENT, doc, "thread_root") for doc in docs}

	def test_split_keys_still_form_one_thread(self):
		a, b, c = self.split_conversation()
		docs = [archiving.archive(name) for name in (a, b, c)]
		keys = {frappe.db.get_value(DOCUMENT, doc, "thread_key") for doc in docs}
		self.assertEqual(len(keys), 2)
		for doc in docs:
			self.assertEqual(self.names(thread.thread(doc)), docs[::-1])
		self.assertEqual(len(self.roots(*docs)), 1)

	def test_archived_out_of_order(self):
		a, b, c = self.split_conversation()
		c_doc = archiving.archive(c)
		a_doc = archiving.archive(a)
		self.assertEqual(len(self.roots(a_doc, c_doc)), 2)
		b_doc = archiving.archive(b)
		self.assertEqual(len(self.roots(a_doc, b_doc, c_doc)), 1)
		self.assertEqual(self.names(thread.thread(a_doc)), [c_doc, b_doc, a_doc])

	def test_transitive_without_roots(self):
		a, b, c = self.split_conversation()
		docs = [archiving.archive(name) for name in (a, b, c)]
		for doc in docs:
			frappe.db.set_value(DOCUMENT, doc, "thread_root", None)
		self.assertEqual(self.names(thread.thread(docs[2])), docs[::-1])

	def test_missing_middle_mail_is_one_gap(self):
		a_id, b_id = message_id(), message_id()
		a = self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		c = self.mail(
			"2026-03-12 09:00:00",
			mime(text="c", In_Reply_To=f"<{b_id}>", References=references(a_id, b_id)),
		)
		a_doc, c_doc = archiving.archive(a), archiving.archive(c)
		self.assertEqual(self.names(thread.thread(c_doc)), [c_doc, ("gap", b_id), a_doc])

	def test_backfill_joins_split_threads(self):
		a, b, c = self.split_conversation()
		docs = [archiving.archive(name) for name in (a, b, c)]
		for doc in docs:
			key = frappe.db.get_value(DOCUMENT, doc, "thread_key")
			frappe.db.set_value(DOCUMENT, doc, "thread_root", key)
		self.assertEqual(len(self.roots(*docs)), 2)
		join_mail_threads.execute()
		self.assertEqual(len(self.roots(*docs)), 1)
		self.assertIsNotNone(self.roots(*docs).pop())

	def test_other_company_is_not_joined(self):
		from docarchive.tests.factories import make_own_company

		a, b, c = self.split_conversation()
		a_doc, b_doc = archiving.archive(a), archiving.archive(b)
		frappe.db.set_value(DOCUMENT, a_doc, "own_company", make_own_company("acme-ltd").name)
		c_doc = archiving.archive(c)
		self.assertEqual(self.names(thread.thread(c_doc)), [c_doc, b_doc, ("gap", self.message_of(a_doc))])

	def message_of(self, doc):
		return frappe.db.get_value(DOCUMENT, doc, "mail_message_id")
