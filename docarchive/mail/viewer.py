import base64
import mimetypes
import re
import unicodedata
from urllib.parse import quote, unquote

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.utils.html_utils import sanitize_html

from docarchive import content
from docarchive.mail import access, archiving, headers

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"
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
DROPPED_ATTRIBUTES = {
	"action",
	"data",
	"dynsrc",
	"formaction",
	"lowsrc",
	"ping",
	"poster",
	"srcset",
	"xlink:href",
	"xml:base",
}
LINK_TAGS = {"a", "area"}
CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.IGNORECASE | re.DOTALL)
UNSAFE_CSS = re.compile(
	r"https?:|//|\\|url\(|image-set|image\(|cross-fade|element\(|src\(|expression|@import", re.IGNORECASE
)
UNSAFE_URL = re.compile(r"""[()'"\s\\]""")
FRAGMENT = re.compile(r"#[\w-]+")
LINK = re.compile(r"(https?://|mailto:|tel:)", re.IGNORECASE)
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
REMOTE_CSS = re.compile(r"https?:|//", re.IGNORECASE)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_BYTES = 20 * 1024 * 1024
PLACEHOLDER_SVG = (
	b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
	b'<rect x="0.5" y="0.5" width="119" height="79" fill="#f3f3f3" stroke="#c8c8c8"/>'
	b'<path d="M30 60l20-24 14 16 10-10 16 18z" fill="#c8c8c8"/><circle cx="80" cy="28" r="7" fill="#c8c8c8"/>'
	b"</svg>"
)
IMAGE_PLACEHOLDER = "data:image/svg+xml;base64," + base64.b64encode(PLACEHOLDER_SVG).decode()


@frappe.whitelist()
def render(name: str, doctype: str = DOCUMENT) -> dict:
	raw, sha256, origin = _load(name, doctype)
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
				"size": _size(part),
			}
			for index, part in enumerate(message.iter_attachments())
		],
		"sha256_ok": content.sha256(raw) == sha256,
		"origin": origin,
	}


@frappe.whitelist()
def attachment(name: str, index: int, doctype: str = DOCUMENT) -> None:
	raw = _load(name, doctype)[0]
	parts = list(headers.message_from(raw).iter_attachments())
	index = int(index)
	if not 0 <= index < len(parts):
		frappe.throw(_("This attachment does not exist."))
	part = parts[index]
	file_name = _file_name(part, index)
	frappe.response.filename = _ascii(file_name) or f"attachment-{index + 1}"
	frappe.response.display_content_as = f"attachment; filename*=UTF-8''{quote(file_name, safe='')}"
	frappe.response.filecontent = _bytes(part)
	frappe.response.content_type = (
		part.get_content_type() if part["Content-Type"] else "application/octet-stream"
	)
	frappe.response.type = "download"


def _load(name, doctype=DOCUMENT):
	if doctype == ORIGINAL:
		return _load_original(name)
	if doctype != DOCUMENT:
		frappe.throw(_("Only documents and mail originals can be shown."))
	frappe.has_permission(DOCUMENT, "read", name, throw=True)
	doc = frappe.get_doc(DOCUMENT, name)
	if not (doc.file or "").lower().endswith(".eml"):
		frappe.throw(_("This document is not an archived e-mail."))
	raw = content.read_bytes(doc.file)
	if raw is None:
		frappe.throw(_("The e-mail file of this document is missing."))
	return raw, doc.content_sha256, (archiving.mail_meta(doc) or {}).get("origin")


def _load_original(name):
	frappe.has_permission(ORIGINAL, "read", name, throw=True)
	original = frappe.db.get_value(
		ORIGINAL, name, ["communication", "file", "sha256", "origin"], as_dict=True
	)
	if not original.communication or original.communication not in access.readable([original.communication]):
		frappe.throw(_("You cannot read this e-mail."), frappe.PermissionError)
	reason = archiving.refusal(name)
	if reason:
		frappe.throw(reason)
	raw = content.read_bytes(original.file) if original.file else None
	if raw is None:
		frappe.throw(_("The captured original of this e-mail is missing."))
	return raw, original.sha256, original.origin


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
		return b"".join(inner.as_bytes() for inner in part.get_payload())
	if part.is_multipart():
		return part.as_bytes()
	return part.get_payload(decode=True) or b""


def _size(part):
	payload = part.get_payload()
	if not isinstance(payload, str) or part.get("Content-Transfer-Encoding", "").strip().lower() != "base64":
		return len(_bytes(part))
	characters = len(payload) - sum(payload.count(space) for space in " \t\r\n")
	end = payload.rstrip()
	return max(characters * 3 // 4 - (len(end) - len(end.rstrip("="))), 0)


def _file_name(part, index):
	name = CONTROL.sub("", re.split(r"[/\\]", part.get_filename() or "")[-1]).strip()
	if name not in ("", ".", ".."):
		return name
	extension = ".eml" if part.get_content_type() == "message/rfc822" else ""
	return f"attachment-{index + 1}{extension or mimetypes.guess_extension(part.get_content_type()) or ''}"


def _ascii(value):
	return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().replace("\\", "")


def _html(message):
	body = _text(message.get_body(preferencelist=("html",)))
	if body is None:
		return None, False
	clean = sanitize_html(body, always_sanitize=True, disallowed_tags=BLOCKED_TAGS)
	return _Rewriter(_inline_images(message)).run(clean)


def _inline_images(message):
	images, total = {}, 0
	for part in message.walk():
		cid = part.get("Content-ID")
		if not cid or part.get_content_maintype() != "image":
			continue
		key, size = str(cid).strip().strip("<>"), _size(part)
		if size > MAX_IMAGE_BYTES or total + size > MAX_IMAGES_BYTES:
			images[key] = IMAGE_PLACEHOLDER
			continue
		total += size
		data = base64.b64encode(_bytes(part)).decode()
		images[key] = f"data:{part.get_content_type()};base64,{data}"
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
			if attribute == "href" and not self._link(tag, str(value).strip()):
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

	def _link(self, tag, href):
		return tag.name in LINK_TAGS and bool(LINK.match(href) or FRAGMENT.fullmatch(href))

	def _css(self, tag, attribute, value):
		outside = CSS_URL.sub("none", value)
		urls = [m.group(2).strip() for m in CSS_URL.finditer(value)]
		if UNSAFE_CSS.search(outside) or any(UNSAFE_URL.search(url) for url in urls):
			self.remote = self.remote or bool(REMOTE_CSS.search(value))
			del tag[attribute]
		else:
			tag[attribute] = CSS_URL.sub(lambda m: self._css_url(m.group(2).strip()), value)

	def _css_url(self, url):
		resolved = url if FRAGMENT.fullmatch(url) else self._resolve(url)
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
