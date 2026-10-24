import base64
import hashlib
from datetime import UTC, datetime

import frappe
from frappe import _

from docarchive import storage_targets

MODES = ("GOVERNANCE", "COMPLIANCE")
CLIENT_FIELDS = ("endpoint", "region", "bucket", "access_key", "secret_key")
MISSING = ("404", "NoSuchKey", "NotFound")
SMOKE_PREFIX = "_smoke/"


class LockError(frappe.ValidationError):
	pass


class Target:
	def __init__(self, name, bucket, lock_mode, client):
		self.name = name
		self.bucket = bucket
		self.lock_mode = lock_mode
		self._client = client


def client(config):
	from s3storage.client import get_client

	return get_client(config={field: config[field] for field in CLIENT_FIELDS})[0]


def from_config(config) -> Target:
	return Target(config.name, config.bucket, config.lock_mode, client(config))


def targets() -> list[Target] | None:
	configured = storage_targets.configured()
	return [from_config(config) for config in configured] if configured else None


def error_code(error):
	response = getattr(error, "response", None)
	return (response or {}).get("Error", {}).get("Code") if isinstance(response, dict) else None


def validate_lock(mode, retain_until) -> datetime:
	if mode not in MODES:
		raise LockError(_("Invalid lock mode {0}.").format(mode))
	if isinstance(retain_until, str):
		try:
			retain_until = datetime.fromisoformat(retain_until)
		except ValueError:
			raise LockError(_("Invalid retain-until date {0}.").format(retain_until))
	if not isinstance(retain_until, datetime) or retain_until.utcoffset() is None:
		raise LockError(_("The retain-until date must be a date and time with a time zone."))
	if retain_until <= datetime.now(UTC):
		raise LockError(_("The retain-until date must be in the future."))
	return retain_until.astimezone(UTC)


def put_locked(target, key, body, mode, retain_until, content_type="application/octet-stream") -> dict:
	retain_until = validate_lock(mode, retain_until)
	if not key or not isinstance(body, bytes):
		raise LockError(_("An archive object needs a key and bytes."))
	sha256 = hashlib.sha256(body).hexdigest()
	response = target._client.put_object(
		Bucket=target.bucket,
		Key=key,
		Body=body,
		ContentType=content_type,
		ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode(),
		Metadata={"sha256": sha256},
		ObjectLockMode=mode,
		ObjectLockRetainUntilDate=retain_until,
	)
	return {"sha256": sha256, "version_id": response.get("VersionId")}


def head(target, key) -> dict | None:
	try:
		return target._client.head_object(Bucket=target.bucket, Key=key)
	except Exception as e:
		if error_code(e) in MISSING:
			return None
		raise


def read_sha256(target, key) -> str:
	body = target._client.get_object(Bucket=target.bucket, Key=key)["Body"]
	digest = hashlib.sha256()
	for chunk in iter(lambda: body.read(1024 * 1024), b""):
		digest.update(chunk)
	return digest.hexdigest()


def retention(target, key, version_id=None) -> dict:
	kwargs = {"VersionId": version_id} if version_id else {}
	value = target._client.get_object_retention(Bucket=target.bucket, Key=key, **kwargs)["Retention"]
	return {"mode": value.get("Mode"), "retain_until": value.get("RetainUntilDate")}


def smoke_delete(target, key, version_id):
	if not key.startswith(SMOKE_PREFIX):
		raise LockError(_("Only smoke test objects may be deleted."))
	target._client.delete_object(Bucket=target.bucket, Key=key, VersionId=version_id)
