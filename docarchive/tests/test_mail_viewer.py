import uuid
from email.message import EmailMessage

import frappe
from frappe.utils.response import build_response

from docarchive.mail import archiving, thread, viewer
from docarchive.tests.factories import make_manager, make_outsider, make_own_company
from docarchive.tests.test_mail_archiving import MailArchivingTestCase, address

DOCUMENT = "Business Document"
PNG = b"\x89PNG\r\n\x1a\nfake\xff\xff\xff"


def message_id():
	return f"{uuid.uuid4().hex}@example.test"


def mime(text=None, html=None, charset="utf-8", **headers):
	msg = EmailMessage()
	msg["From"] = "Client <client@example.test>"
	msg["To"] = "info@acme.test"
	msg["Cc"] = "boss@example.test"
	msg["Subject"] = "Offer"
	msg["Date"] = "Tue, 10 Mar 2026 09:30:00 +0000"
	msg["Message-ID"] = f"<{message_id()}>"
	for name, value in headers.items():
		del msg[name.replace("_", "-")]
		msg[name.replace("_", "-")] = value
	if text is not None:
		msg.set_content(text, charset=charset)
	if html is not None:
		if text is None:
			msg.set_content(html, subtype="html")
		else:
			msg.add_alternative(html, subtype="html")
	return msg


def references(*ids):
	return " ".join(f"<{i}>" for i in ids)


class TestViewer(MailArchivingTestCase):
	def archived(self, msg, **fields):
		comm = self.communication(**fields)
		return archiving.archive(self.capture(msg.as_bytes(), comm))

	def doc_with_html(self, html):
		return self.archived(mime(text="plain", html=html))

	def test_render_headers_and_text(self):
		msg = mime(text="Hello there")
		r = viewer.render(self.archived(msg))
		self.assertEqual(
			r["headers"],
			{
				"from": "Client <client@example.test>",
				"to": "info@acme.test",
				"cc": "boss@example.test",
				"date": "Tue, 10 Mar 2026 09:30:00 +0000",
				"subject": "Offer",
				"message_id": msg["Message-ID"].strip("<>"),
			},
		)
		self.assertEqual((r["text"].strip(), r["html"], r["remote_images"]), ("Hello there", None, False))
		self.assertEqual((r["attachments"], r["sha256_ok"], r["origin"]), ([], True, "t"))

	def test_script_removed_and_remote_images_blocked(self):
		r = viewer.render(
			self.doc_with_html('<p>Hi</p><script>x()</script><img src="https://t.example/p.gif">')
		)
		self.assertNotIn("<script", r["html"])
		self.assertIn('data-remote-src="https://t.example/p.gif"', r["html"])
		self.assertNotIn(' src="https://', r["html"])
		self.assertTrue(r["remote_images"])
		self.assertEqual(r["text"].strip(), "plain")

	def test_dangerous_markup_removed(self):
		html = (
			'<p onclick="x()">a</p><a href="javascript:alert(1)">j</a>'
			'<form action="https://x.example"><input name="q"><button>b</button></form>'
			'<iframe src="https://x.example"></iframe><object data="x"></object>'
			'<link rel="stylesheet" href="https://x.example/c.css"><meta http-equiv="refresh" content="0">'
			'<video poster="https://x.example/p.png" src="https://x.example/v.mp4"></video>'
		)
		out = viewer.render(self.doc_with_html(html))["html"]
		for fragment in ("onclick", "javascript:", "<form", "<input", "<button", "<iframe", "<object"):
			self.assertNotIn(fragment, out)
		for fragment in ("<link", "<meta", "<video", "x.example"):
			self.assertNotIn(fragment, out)
		self.assertIn("<p>a</p>", out)

	def test_remote_css_and_srcset_blocked(self):
		html = (
			"<style>p{background:url(https://t.example/s.png)}</style>"
			"<p style=\"background-image: url('https://t.example/i.png'); color: red\">s</p>"
			'<img src="https://t.example/a.png" srcset="https://t.example/b.png 2x" data-remote-src="x">'
			'<table background="https://t.example/bg.png"><tr><td>c</td></tr></table>'
		)
		r = viewer.render(self.doc_with_html(html))
		self.assertNotIn("url(https", r["html"])
		self.assertNotIn("url('https", r["html"])
		self.assertNotIn("srcset", r["html"])
		self.assertNotIn('background="https', r["html"])
		self.assertEqual(r["html"].count("https://t.example"), 1)
		self.assertIn('data-remote-src="https://t.example/a.png"', r["html"])
		self.assertIn("color:red", r["html"].replace(" ", ""))
		self.assertTrue(r["remote_images"])

	def test_css_url_fragment_bypass_blocked(self):
		html = (
			'<p style="background-image:url(&quot;#a), url(https://evil.example/x&quot;)">d</p>'
			"<p style=\"background-image:url('#a), url(https://evil.example/y')\">s</p>"
			'<svg><rect fill="url(&quot;#a), url(https://evil.example/z&quot;)"></rect>'
			'</svg><p style="background-image: url(#grad)">g</p>'
		)
		r = viewer.render(self.doc_with_html(html))
		self.assertNotIn("evil.example", r["html"])
		self.assertIn("url(#grad)", r["html"])
		self.assertTrue(r["remote_images"])

	def test_ping_and_xml_base_dropped(self):
		html = '<a href="https://ok.example/" ping="https://t.example/p">ok</a><p xml:base="https://t.example/">b</p>'
		out = viewer.render(self.doc_with_html(html))["html"]
		self.assertNotIn("t.example", out)
		self.assertIn('href="https://ok.example/"', out)

	def test_only_absolute_safe_links_kept(self):
		html = (
			'<a href="/api/method/logout">rel</a><a href="logout">rel2</a><a href="ftp://x.example/">ftp</a>'
			'<a href="https://ok.example/">h</a><a href="mailto:a@example.test">m</a>'
			'<a href="tel:+49301234">t</a><a href="#top">f</a>'
		)
		out = viewer.render(self.doc_with_html(html))["html"]
		for fragment in ("/api/method/logout", 'href="logout"', "ftp:"):
			self.assertNotIn(fragment, out)
		for fragment in ("https://ok.example/", "mailto:a@example.test", "tel:+49301234", 'href="#top"'):
			self.assertIn(fragment, out)

	def test_cid_image_inlined(self):
		msg = mime(
			text="plain", html='<p>Logo</p><img src="cid:logo@x"><p style="background:url(cid:logo@x)">b</p>'
		)
		msg.get_payload()[1].add_related(PNG, "image", "png", cid="<logo@x>")
		r = viewer.render(self.archived(msg))
		self.assertIn('src="data:image/png;base64,', r["html"])
		self.assertIn("url(data:image/png;base64,", r["html"])
		self.assertNotIn("cid:", r["html"])
		self.assertFalse(r["remote_images"])

	def test_attachments_listed_and_downloadable(self):
		msg = mime(text="see attached")
		msg.add_attachment(b"%PDF-1.7 one", maintype="application", subtype="pdf", filename="offer.pdf")
		msg.add_attachment("second", subtype="plain", filename="notes.txt")
		name = self.archived(msg)
		r = viewer.render(name)
		self.assertEqual(
			r["attachments"],
			[
				{"index": 0, "file_name": "offer.pdf", "content_type": "application/pdf", "size": 12},
				{"index": 1, "file_name": "notes.txt", "content_type": "text/plain", "size": 7},
			],
		)
		viewer.attachment(name, "0")
		self.assertEqual(
			(frappe.response.filename, frappe.response.filecontent, frappe.response.type),
			("offer.pdf", b"%PDF-1.7 one", "download"),
		)
		for index in (2, -1):
			with self.subTest(index=index), self.assertRaises(frappe.ValidationError):
				viewer.attachment(name, index)
		self.assertRaises(frappe.FrappeTypeError, viewer.attachment, name, "x")

	def test_attachment_download_name_and_type(self):
		msg = mime(text="x")
		msg.add_attachment(b"pdf", maintype="application", subtype="pdf", filename="../dir\\Grüße\r\n.pdf")
		name = self.archived(msg)
		self.assertEqual(viewer.render(name)["attachments"][0]["file_name"], "Grüße.pdf")
		viewer.attachment(name, 0)
		response = build_response("download")
		self.assertEqual(response.mimetype, "application/pdf")
		self.assertIn("filename*=UTF-8''Gr%C3%BC%C3%9Fe.pdf", response.headers["Content-Disposition"])
		disposition = response.headers["Content-Disposition"]
		self.assertIn("filename=Grue.pdf", disposition)
		self.assertTrue(disposition.isascii())
		self.assertNotIn("\n", disposition)

	def test_attachment_without_declared_type_is_octet_stream(self):
		raw = (
			b"From: a@example.test\r\nSubject: x\r\nMIME-Version: 1.0\r\n"
			b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
			b"--b\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
			b'--b\r\nContent-Disposition: attachment; filename="blob.txt"\r\n\r\ndata\r\n--b--\r\n'
		)
		name = archiving.archive(self.capture(raw))
		viewer.attachment(name, 0)
		self.assertEqual(build_response("download").mimetype, "application/octet-stream")

	def test_empty_attached_message_renders(self):
		raw = (
			b"From: a@example.test\r\nSubject: x\r\nMIME-Version: 1.0\r\n"
			b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
			b"--b\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
			b"--b\r\nContent-Type: message/rfc822\r\n\r\n--b--\r\n"
		)
		name = archiving.archive(self.capture(raw))
		r = viewer.render(name)
		self.assertEqual(r["text"].strip(), "body")
		self.assertEqual(r["attachments"][0]["content_type"], "message/rfc822")
		viewer.attachment(name, 0)
		self.assertIsInstance(frappe.response.filecontent, bytes)

	def test_message_part_without_payload(self):
		part = EmailMessage()
		part["Content-Type"] = "message/rfc822"
		part.set_payload([])
		self.assertEqual(viewer._bytes(part), b"")

	def test_sha_mismatch_reported(self):
		name = self.archived(mime(text="x"))
		frappe.db.set_value(DOCUMENT, name, "content_sha256", "0" * 64)
		self.assertFalse(viewer.render(name)["sha256_ok"])

	def test_latin1_body_decoded(self):
		msg = mime(text="Grüße aus Köln", charset="iso-8859-1")
		self.assertIn(b"iso-8859-1", msg.as_bytes())
		self.assertEqual(viewer.render(self.archived(msg))["text"].strip(), "Grüße aus Köln")

	def test_undeclared_8bit_body_still_renders(self):
		raw = b"From: a@example.test\r\nSubject: x\r\nContent-Type: text/plain; charset=unknown-8\r\n\r\nK\xf6ln\r\n"
		r = viewer.render(archiving.archive(self.capture(raw)))
		self.assertEqual(r["text"].strip(), "K\ufffdln")

	def test_render_needs_eml_file(self):
		name = self.archived(mime(text="x"))
		frappe.db.set_value(DOCUMENT, name, "file", "/private/files/offer.pdf")
		self.assertRaises(frappe.ValidationError, viewer.render, name)

	def test_render_needs_read_permission(self):
		name = self.archived(mime(text="x"))
		frappe.set_user(make_outsider(address("outsider")))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			viewer.render(name)
		with self.assertRaises(frappe.PermissionError):
			viewer.attachment(name, 0)
		with self.assertRaises(frappe.PermissionError):
			thread.thread(name)

	def test_render_after_communication_deleted(self):
		comm = self.communication()
		name = archiving.archive(self.capture(mime(text="kept").as_bytes(), comm))
		frappe.delete_doc("Communication", comm.name)
		self.assertEqual(viewer.render(name)["text"].strip(), "kept")


class TestThread(MailArchivingTestCase):
	def mail(self, at, msg):
		comm = self.communication(communication_date=at)
		return comm, self.capture(msg.as_bytes(), comm)

	def test_thread_orders_and_shows_gap(self):
		a_id, b_id = message_id(), message_id()
		_, a = self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		comm_b, _ = self.mail(
			"2026-03-11 09:00:00",
			mime(text="b", Message_ID=f"<{b_id}>", In_Reply_To=f"<{a_id}>", References=references(a_id)),
		)
		_, c = self.mail(
			"2026-03-12 09:00:00",
			mime(text="c", In_Reply_To=f"<{b_id}>", References=references(a_id, b_id)),
		)
		c_doc = archiving.archive(c)
		a_doc = archiving.archive(a)
		items = thread.thread(c_doc)
		self.assertEqual([i["kind"] for i in items], ["mail", "gap", "mail"])
		self.assertEqual([items[0]["name"], items[2]["name"]], [a_doc, c_doc])
		self.assertEqual((items[1]["state"], items[1]["communication"]), ("captured", comm_b.name))
		self.assertEqual(items[1]["message_id"], b_id)
		self.assertEqual(
			{k: items[2][k] for k in ("title", "channel", "direction", "sender")},
			{"title": "Offer", "channel": "E-mail", "direction": "Incoming", "sender": "client@example.test"},
		)
		self.assertEqual(str(items[2]["at"]), "2026-03-12 09:00:00")

	def test_thread_gaps_from_references(self):
		a_id, x_id, b_id = message_id(), message_id(), message_id()
		self.communication(message_id=b_id)
		_, c = self.mail(
			"2026-03-12 09:00:00",
			mime(text="c", In_Reply_To=f"<{b_id}>", References=references(a_id, x_id, b_id)),
		)
		items = thread.thread(archiving.archive(c))
		self.assertEqual(
			[(i["kind"], i.get("state")) for i in items],
			[("gap", "unknown"), ("gap", "unknown"), ("gap", "no_original"), ("mail", None)],
		)
		self.assertEqual([i.get("message_id") for i in items[:3]], [a_id, x_id, b_id])
		self.assertEqual(items[2]["communication"], None)

	def test_captured_gap_needs_readable_communication(self):
		a_id = message_id()
		self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		_, c = self.mail("2026-03-12 09:00:00", mime(text="c", References=references(a_id)))
		c_doc = archiving.archive(c)
		self.assertEqual(thread.thread(c_doc)[0]["state"], "captured")
		frappe.set_user(make_manager(address("manager")))
		self.addCleanup(frappe.set_user, "Administrator")
		items = thread.thread(c_doc)
		self.assertEqual((items[0]["state"], items[0]["communication"]), ("unknown", None))

	def test_gap_archived_under_other_thread(self):
		a_id = message_id()
		_, a = self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		_, c = self.mail("2026-03-12 09:00:00", mime(text="c", References=references(a_id)))
		a_doc, c_doc = archiving.archive(a), archiving.archive(c)
		frappe.db.set_value(DOCUMENT, a_doc, "thread_key", "elsewhere")
		frappe.set_user(make_manager(address("manager")))
		self.addCleanup(frappe.set_user, "Administrator")
		gap = thread.thread(c_doc)[0]
		self.assertEqual(
			(gap["kind"], gap["state"], gap["business_document"], gap["communication"]),
			("gap", "archived", a_doc, None),
		)

	def test_gap_archived_for_other_company_not_shown(self):
		a_id = message_id()
		_, a = self.mail("2026-03-10 09:00:00", mime(text="a", Message_ID=f"<{a_id}>"))
		_, c = self.mail("2026-03-12 09:00:00", mime(text="c", References=references(a_id)))
		a_doc, c_doc = archiving.archive(a), archiving.archive(c)
		frappe.db.set_value(DOCUMENT, a_doc, "own_company", make_own_company("acme-ltd").name)
		gap = thread.thread(c_doc)[0]
		self.assertEqual((gap["state"], gap["business_document"]), ("unknown", None))

	def test_other_company_and_thread_excluded(self):
		_, c = self.mail("2026-03-12 09:00:00", mime(text="c"))
		c_doc = archiving.archive(c)
		key = frappe.db.get_value(DOCUMENT, c_doc, "thread_key")
		_, other = self.mail("2026-03-13 09:00:00", mime(text="o", References=references(key)))
		other_doc = archiving.archive(other)
		frappe.db.set_value(DOCUMENT, other_doc, "own_company", make_own_company("acme-ltd").name)
		_, unrelated = self.mail("2026-03-13 09:00:00", mime(text="u"))
		archiving.archive(unrelated)
		self.assertEqual([i["name"] for i in thread.thread(c_doc)], [c_doc])

	def test_no_thread_key_gives_empty_list(self):
		_, c = self.mail("2026-03-12 09:00:00", mime(text="c"))
		name = archiving.archive(c)
		frappe.db.set_value(DOCUMENT, name, "thread_key", None)
		self.assertEqual(thread.thread(name), [])
