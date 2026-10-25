import base64
import mimetypes
import re
from urllib.parse import unquote

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.utils.html_utils import sanitize_html

from docarchive import content
from docarchive.mail import headers

DOCUMENT = "Business Document"
HEADERS = {"from": "From", "to": "To", "cc": "Cc", "date": "Date", "subject": "Subject"}
BLOCKED_TAGS = {
	"audio",
	"button",
	"canvas",
	"command",
	"datalist",
	"dialog",
	"event-source",
	"form",
	"input",
	"keygen",
	"link",
	"meta",
	"optgroup",
	"option",
	"output",
	"select",
	"sound",
	"source",
	"textarea",
	"video",
}
DROPPED_ATTRIBUTES = {"action", "data", "dynsrc", "formaction", "lowsrc", "poster", "srcset", "xlink:href"}
LINK_TAGS = {"a", "area"}
CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.IGNORECASE | re.DOTALL)
UNSAFE_CSS = re.compile(r"https?:|//|\\|image-set|expression|@import", re.IGNORECASE)
REMOTE_CSS = re.compile(r"https?:|//", re.IGNORECASE)


@frappe.whitelist()
def render(name: str) -> dict:
	doc, raw = _load(name)
	message = headers.message_from(raw)
	html, remote = _html(message)
	return {
		"headers": _headers(message),
		"html": html,
		"text": _text(message.get_body(preferencelist=("plain",))),
		"remote_images": remote,
		"attachments": [
			{
				"index": index,
				"file_name": _file_name(part, index),
				"content_type": part.get_content_type(),
				"size": len(_bytes(part)),
			}
			for index, part in enumerate(message.iter_attachments())
		],
		"sha256_ok": content.sha256(raw) == doc.content_sha256,
	}


@frappe.whitelist()
def attachment(name: str, index: int) -> None:
	_doc, raw = _load(name)
	parts = list(headers.message_from(raw).iter_attachments())
	index = int(index)
	if not 0 <= index < len(parts):
		frappe.throw(_("This attachment does not exist."))
	frappe.response.filename = _file_name(parts[index], index)
	frappe.response.filecontent = _bytes(parts[index])
	frappe.response.type = "download"


def _load(name):
	frappe.has_permission(DOCUMENT, "read", name, throw=True)
	doc = frappe.get_doc(DOCUMENT, name)
	if not (doc.file or "").lower().endswith(".eml"):
		frappe.throw(_("This document is not an archived e-mail."))
	raw = content.read_bytes(doc.file)
	if raw is None:
		frappe.throw(_("The e-mail file of this document is missing."))
	return doc, raw


def _headers(message):
	values = {key: headers.header(message, name) for key, name in HEADERS.items()}
	values["message_id"] = (headers.ids(headers.header(message, "Message-ID")) or [""])[0]
	return values


def _text(part):
	if part is None:
		return None
	try:
		return part.get_content()
	except LookupError, UnicodeError:
		return (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")


def _bytes(part):
	if part.get_content_maintype() == "message":
		return part.get_payload(0).as_bytes()
	return part.get_payload(decode=True) or b""


def _file_name(part, index):
	if part.get_filename():
		return part.get_filename()
	extension = ".eml" if part.get_content_type() == "message/rfc822" else ""
	return f"attachment-{index + 1}{extension or mimetypes.guess_extension(part.get_content_type()) or ''}"


def _html(message):
	body = _text(message.get_body(preferencelist=("html",)))
	if body is None:
		return None, False
	clean = sanitize_html(body, always_sanitize=True, disallowed_tags=BLOCKED_TAGS)
	return _Rewriter(_inline_images(message)).run(clean)


def _inline_images(message):
	images = {}
	for part in message.walk():
		cid = part.get("Content-ID")
		if cid and part.get_content_maintype() == "image":
			data = base64.b64encode(_bytes(part)).decode()
			images[str(cid).strip().strip("<>")] = f"data:{part.get_content_type()};base64,{data}"
	return images


class _Rewriter:
	def __init__(self, images):
		self.images = images
		self.remote = False

	def run(self, html):
		soup = BeautifulSoup(html, "html.parser")
		for tag in soup.find_all(True):
			self._tag(tag)
		return str(soup), self.remote

	def _tag(self, tag):
		for attribute in list(tag.attrs):
			if attribute.startswith("data-") or attribute in DROPPED_ATTRIBUTES:
				del tag[attribute]
		for attribute, value in list(tag.attrs.items()):
			if attribute == "href" and tag.name not in LINK_TAGS:
				del tag[attribute]
			elif attribute in ("src", "background"):
				self._source(tag, attribute, value)
			elif attribute == "style" or "url(" in str(value).lower():
				self._css(tag, attribute, str(value))

	def _source(self, tag, attribute, value):
		value = str(value).strip()
		resolved = self._resolve(value)
		del tag[attribute]
		if resolved:
			tag[attribute] = resolved
		elif self._is_remote(value):
			self.remote = True
			if tag.name == "img" and attribute == "src":
				tag["data-remote-src"] = value

	def _css(self, tag, attribute, value):
		outside = CSS_URL.sub("none", value)
		if UNSAFE_CSS.search(outside):
			self.remote = self.remote or bool(REMOTE_CSS.search(outside))
			del tag[attribute]
		else:
			tag[attribute] = CSS_URL.sub(lambda m: self._css_url(m.group(2).strip()), value)

	def _css_url(self, url):
		resolved = url if url.startswith("#") else self._resolve(url)
		if resolved:
			return f"url({resolved})"
		self.remote = self.remote or self._is_remote(url)
		return "none"

	def _resolve(self, url):
		if url.lower().startswith("cid:"):
			return self.images.get(unquote(url[4:]).strip("<>"))
		return None

	def _is_remote(self, url):
		return url.lower().startswith(("http:", "https:", "//"))
