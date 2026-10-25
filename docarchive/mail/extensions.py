import frappe

from docarchive.mail import originals

RECEIVE_SAVEPOINT = "docarchive_mail_receive"


class EmailAccountArchive:
	def get_inbound_mails(self):
		mails = super().get_inbound_mails()
		origin = "frappe-mail" if self.service == "Frappe Mail" else "frappe-receive"
		for mail in mails:
			mail.process = _capturing(mail, mail.process, origin)
		return mails


def _capturing(mail, process, origin):
	def wrapper(*args, **kwargs):
		communication = process(*args, **kwargs)
		if communication:
			_capture(communication, mail.raw_message, origin)
		return communication

	return wrapper


def _capture(communication, raw, origin):
	try:
		frappe.db.savepoint(RECEIVE_SAVEPOINT)
		originals.capture(communication, raw, origin)
	except Exception:
		originals.fail(RECEIVE_SAVEPOINT)


class EmailQueueArchive:
	def send(self, *args, **kwargs):
		sent_before = originals.sent_count(self)
		result = super().send(*args, **kwargs)
		originals.capture_sent(self, sent_before)
		return result
