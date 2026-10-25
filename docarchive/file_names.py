import datetime
import re

MIN_YEAR = 1990
FILE_DATE = re.compile(r"(?<!\d)(?:(\d{4})([-_]?)(\d{2})\2(\d{2})|(\d{2})\.(\d{2})\.(\d{4}))(?!\d)", re.ASCII)
SEPARATORS = re.compile(r"[\s_-]+")
EDGES = " .,;:_-"


def stem(file_name):
	name = (file_name or "").rsplit("/", 1)[-1]
	base, dot, _ = name.rpartition(".")
	return base if dot and base else name


def title_from_text(text):
	return SEPARATORS.sub(" ", text).strip()


def valid_date(year, month, day, today):
	if not MIN_YEAR <= year <= today.year + 1:
		return None
	try:
		return datetime.date(year, month, day)
	except ValueError:
		return None


def date_from_file_name(file_name, today=None):
	today = today or datetime.date.today()
	base = stem(file_name)
	for match in FILE_DATE.finditer(base):
		year, month, day = (match[1], match[3], match[4]) if match[1] else (match[7], match[6], match[5])
		date = valid_date(int(year), int(month), int(day), today)
		if date:
			rest = f"{base[: match.start()]} {base[match.end() :]}"
			return date, title_from_text(rest).strip(EDGES) or title_from_text(base)
	return None, title_from_text(base)
