import ipaddress
import re
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

from docarchive.slug import validate_slug

SITE_CONFIG = "Site Config"
FIELDS = "Fields"
LOCATION = ("endpoint", "region", "bucket")
CREDENTIALS = ("access_key", "secret_key")
ADMIN_CREDENTIALS = ("admin_access_key", "admin_secret_key")
CONFIG_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
LOCAL_NAMES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def validate(settings):
	names, places = set(), set()
	for row in settings.storage_targets:
		validate_slug(row.target_name, "Target Name")
		if row.target_name in names:
			frappe.throw(_("There is more than one storage target named {0}.").format(row.target_name))
		names.add(row.target_name)
		place = validate_row(row)
		if place in places:
			frappe.throw(
				_("Storage target {0} points to the same bucket as another target.").format(row.target_name)
			)
		places.add(place)
	validate_renames(settings)
	if settings.enabled:
		validate_enabled(settings)


def validate_renames(settings):
	before = settings.get_doc_before_save()
	old_names = {row.name: row.target_name for row in before.storage_targets} if before else {}
	for row in settings.storage_targets:
		old = old_names.get(row.name)
		if old and old != row.target_name and frappe.db.exists("Archive Copy", {"target_name": old}):
			frappe.throw(
				_("Storage target {0} holds archived copies, so its name cannot change.").format(old)
			)


def validate_row(row):
	from_fields = row.config_source == FIELDS
	if from_fields:
		if not (row.access_key and row.secret_key):
			frappe.throw(
				_("Storage target {0} needs an access key and a secret key.").format(row.target_name)
			)
		if bool(row.admin_access_key) != bool(row.admin_secret_key):
			frappe.throw(
				_(
					"Storage target {0} needs both an admin access key and an admin secret key, or neither."
				).format(row.target_name)
			)
	else:
		validate_key_names(row)
	place = location(row)
	if (place.endpoint or from_fields) and not (place.endpoint or "").startswith("https://"):
		frappe.throw(
			_("The endpoint of storage target {0} must start with https://.").format(row.target_name)
		)
	if place.endpoint and is_internal_host(place.endpoint):
		frappe.throw(_("The endpoint of storage target {0} must be a public host.").format(row.target_name))
	if (place.bucket or from_fields) and not BUCKET.match(place.bucket or ""):
		frappe.throw(_("Storage target {0} has no valid bucket name.").format(row.target_name))
	if from_fields and not place.region:
		frappe.throw(_("Storage target {0} needs a region.").format(row.target_name))
	if place.endpoint and place.bucket:
		return (place.endpoint.rstrip("/"), place.bucket)
	return (SITE_CONFIG, row.config_prefix)


def is_internal_host(endpoint) -> bool:
	host = (urlsplit(endpoint).hostname or "").lower().rstrip(".")
	if not host or host == "localhost" or host.endswith(LOCAL_NAMES):
		return True
	try:
		address = ipaddress.ip_address(host)
	except ValueError:
		return "." not in host
	return not address.is_global


def validate_key_names(row):
	for label, value in (
		("Site Config Prefix", row.config_prefix),
		("Access Key Config Key", row.access_key_config_key),
		("Secret Key Config Key", row.secret_key_config_key),
	):
		if not CONFIG_KEY.match(value or ""):
			frappe.throw(
				_("{0} of storage target {1} must be a site config key name.").format(
					_(label), row.target_name
				)
			)
	admin = (row.admin_access_key_config_key, row.admin_secret_key_config_key)
	if any(admin) and not all(CONFIG_KEY.match(value or "") for value in admin):
		frappe.throw(
			_(
				"Storage target {0} needs both admin key config keys as site config key names, or neither."
			).format(row.target_name)
		)


def location(row) -> frappe._dict:
	if row.config_source == SITE_CONFIG:
		return frappe._dict({name: frappe.conf.get(f"{row.config_prefix}_{name}") for name in LOCATION})
	return frappe._dict({name: row.get(name) for name in LOCATION})


def validate_enabled(settings):
	targets = [row for row in settings.storage_targets if row.enabled]
	if not targets:
		frappe.throw(_("Enable at least one storage target before enabling the archive."))
	for row in targets:
		missing = missing_config(row)
		if missing:
			frappe.throw(
				_("Storage target {0} misses these site config keys: {1}").format(
					row.target_name, ", ".join(missing)
				)
			)


def config_keys(row) -> list[str]:
	return [
		*(f"{row.config_prefix}_{name}" for name in LOCATION),
		row.access_key_config_key,
		row.secret_key_config_key,
	]


def missing_config(row) -> list[str]:
	if row.config_source != SITE_CONFIG:
		return []
	return [key for key in config_keys(row) if not frappe.conf.get(key)]


def resolve(settings=None) -> list[frappe._dict]:
	settings = settings or frappe.get_cached_doc("Archive Settings")
	return [target(row) for row in settings.storage_targets if row.enabled]


def target(row) -> frappe._dict:
	if row.config_source == SITE_CONFIG:
		credentials = {
			"access_key": frappe.conf.get(row.access_key_config_key),
			"secret_key": frappe.conf.get(row.secret_key_config_key),
			"admin_access_key": row.admin_access_key_config_key
			and frappe.conf.get(row.admin_access_key_config_key),
			"admin_secret_key": row.admin_secret_key_config_key
			and frappe.conf.get(row.admin_secret_key_config_key),
		}
	else:
		credentials = {
			name: get_decrypted_password(row.doctype, row.name, name, raise_exception=False)
			for name in (*CREDENTIALS, *ADMIN_CREDENTIALS)
		}
	return frappe._dict(name=row.target_name, lock_mode=row.lock_mode, **location(row), **credentials)


def configured() -> list[frappe._dict] | None:
	targets = resolve()
	complete = all(t.get(name) for t in targets for name in (*LOCATION, *CREDENTIALS))
	return targets if targets and complete else None
