import email
import email.policy
import re
from email.utils import getaddresses

import frappe

ANGLE = re.compile(r"<([^<>]*)>")
BULK_PRECEDENCE = {"bulk", "list", "junk"}


def message_from(raw: bytes):
	try:
		return email.message_from_bytes(raw, policy=email.policy.default)
	except Exception:
		return email.message.EmailMessage()


def parse(raw: bytes) -> frappe._dict:
	message = message_from(raw)
	return frappe._dict(
		message_id=(ids(header(message, "Message-ID")) or [""])[0],
		in_reply_to=(ids(header(message, "In-Reply-To")) or [""])[0],
		references=ids(header(message, "References")),
		sender=_addresses(header(message, "From")),
		to=_addresses(header(message, "To")),
		cc=_addresses(header(message, "Cc")),
		subject=header(message, "Subject"),
		date_header=header(message, "Date"),
		automated=_automated(message),
	)


def thread_key(parsed, sha256: str) -> str:
	return (parsed.references or [None])[0] or parsed.in_reply_to or parsed.message_id or f"sha256:{sha256}"


def is_internal(parsed, domains: set[str]) -> bool:
	addresses = [
		address for value in (parsed.sender, parsed.to, parsed.cc) for address in value.split(", ") if address
	]
	return bool(addresses) and all(a.rpartition("@")[2].lower() in domains for a in addresses)


def _addresses(value) -> str:
	return ", ".join(address for _, address in getaddresses([value]) if address)


def header(message, name) -> str:
	try:
		return " ".join(str(message[name] or "").split())
	except Exception:
		return ""


def ids(value) -> list[str]:
	found = ANGLE.findall(value or "")
	return [i.strip() for i in found if i.strip()] or ([value.strip()] if value and value.strip() else [])


def _automated(message) -> bool:
	auto_submitted = header(message, "Auto-Submitted").lower()
	return bool(
		header(message, "List-Unsubscribe")
		or auto_submitted not in ("", "no")
		or header(message, "Precedence").lower() in BULK_PRECEDENCE
	)
