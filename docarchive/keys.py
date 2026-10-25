import json
import re
import unicodedata

NO_COUNTERPARTY = "ohne-gegenpartei"
MAX_SEGMENT = 80
UMLAUTS = str.maketrans(
	{
		"ä": "ae",
		"ö": "oe",
		"ü": "ue",
		"Ä": "Ae",
		"Ö": "Oe",
		"Ü": "Ue",
		"ß": "ss",
		"æ": "ae",
		"Æ": "Ae",
		"ø": "o",
		"Ø": "O",
	}
)
UNSAFE = re.compile(r"[^a-z0-9]+")
KEEP_CASE_UNSAFE = re.compile(r"[^A-Za-z0-9.-]+")
MAX_TITLE = 60


def slug(text, fallback="x"):
	ascii_text = unicodedata.normalize("NFKD", str(text or "").translate(UMLAUTS))
	ascii_text = ascii_text.encode("ascii", "ignore").decode().lower()
	return UNSAFE.sub("-", ascii_text).strip("-")[:MAX_SEGMENT].strip("-") or fallback


def keep_case(text) -> str:
	"""Like slug, but keeps upper case, so IDs and numbers stay as people know them."""
	ascii_text = unicodedata.normalize("NFKD", str(text or "").translate(UMLAUTS))
	ascii_text = ascii_text.encode("ascii", "ignore").decode()
	return KEEP_CASE_UNSAFE.sub("-", ascii_text).strip("-.")[:MAX_SEGMENT].strip("-.")


def file_base(name, date, number=None, title=None) -> str:
	"""<doc-id>_<date>_<number or title slug>, without extension. The doc ID keeps keys unique."""
	label = keep_case(number) or slug(title, "")[:MAX_TITLE].strip("-")
	return "_".join(part for part in (keep_case(name) or slug(name), str(date), label) if part)


def object_key(doc, type_slug, ext) -> str:
	date = str(doc.document_date)
	file_name = file_base(doc.name, date, doc.document_number, doc.get("title"))
	return "/".join(
		(
			slug(doc.own_company),
			date[:4],
			slug(type_slug),
			slug(doc.counterparty, NO_COUNTERPARTY),
			f"{file_name}.{slug(ext, 'bin')}",
		)
	)


def attachment_key(key, number, label, ext) -> str:
	base = key.rpartition(".")[0] or key
	return f"{base}__att-{number}-{slug(label, 'anlage')}.{slug(ext, 'bin')}"


def meta_key(key) -> str:
	return f"{key}.meta.json"


def meta_bytes(meta, mode) -> bytes:
	meta = json.loads(meta) if isinstance(meta, str) else meta
	data = {**meta, "retention": {**meta["retention"], "mode": mode}}
	return (json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
