import email
import email.policy
import re
from email.utils import getaddresses

import frappe

ANGLE = re.compile(r"<([^<>]*)>")
BULK_PRECEDENCE = {"bulk", "list", "junk"}


def parse(raw: bytes) -> frappe._dict:
	try:
		message = email.message_from_bytes(raw, policy=email.policy.default)
	except Exception:
		message = email.message.EmailMessage()
	return frappe._dict(
		message_id=(_ids(_header(message, "Message-ID")) or [""])[0],
		in_reply_to=(_ids(_header(message, "In-Reply-To")) or [""])[0],
		references=_ids(_header(message, "References")),
		sender=_addresses(_header(message, "From")),
		to=_addresses(_header(message, "To")),
		cc=_addresses(_header(message, "Cc")),
		subject=_header(message, "Subject"),
		date_header=_header(message, "Date"),
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


def _header(message, name) -> str:
	try:
		return " ".join(str(message[name] or "").split())
	except Exception:
		return ""


def _ids(value) -> list[str]:
	found = ANGLE.findall(value or "")
	return [i.strip() for i in found if i.strip()] or ([value.strip()] if value and value.strip() else [])


def _automated(message) -> bool:
	auto_submitted = _header(message, "Auto-Submitted").lower()
	return bool(
		_header(message, "List-Unsubscribe")
		or auto_submitted not in ("", "no")
		or _header(message, "Precedence").lower() in BULK_PRECEDENCE
	)
