from docarchive.mail import originals


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
			originals.capture(communication, mail.raw_message, origin)
		return communication

	return wrapper


class EmailQueueArchive:
	def send(self, *args, **kwargs):
		result = super().send(*args, **kwargs)
		originals.capture_sent(self)
		return result
