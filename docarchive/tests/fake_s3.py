import base64
import hashlib
import io
import uuid
from datetime import UTC, datetime

from botocore.exceptions import ClientError


def error(code, operation):
	return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class LockedFakeS3:
	"""An Object Lock bucket in memory. Versions are kept, locked versions cannot be deleted."""

	def __init__(self, fail=()):
		self.versions = {}
		self.calls = []
		self.fail = set(fail)

	def record(self, operation, **kwargs):
		self.calls.append((operation, kwargs))
		if operation in self.fail:
			raise error("InternalError", operation)

	def puts(self):
		return [kwargs for operation, kwargs in self.calls if operation == "put_object"]

	def latest(self, Bucket, Key, VersionId=None, operation="HeadObject"):
		versions = self.versions.get((Bucket, Key)) or []
		for version in reversed(versions):
			if VersionId in (None, version["VersionId"]):
				return version
		raise error("404" if operation == "HeadObject" else "NoSuchKey", operation)

	def put_object(self, Bucket, Key, Body, **kwargs):
		self.record("put_object", Bucket=Bucket, Key=Key, Body=Body, **kwargs)
		mode, until = kwargs.get("ObjectLockMode"), kwargs.get("ObjectLockRetainUntilDate")
		md5 = kwargs.get("ContentMD5")
		if (mode or until) and not md5:
			raise error("InvalidRequest", "PutObject")
		if md5 and md5 != base64.b64encode(hashlib.md5(Body).digest()).decode():
			raise error("BadDigest", "PutObject")
		if bool(mode) != bool(until):
			raise error("InvalidRequest", "PutObject")
		version = {
			"VersionId": uuid.uuid4().hex,
			"Body": bytes(Body),
			"Metadata": dict(kwargs.get("Metadata") or {}),
			"ContentType": kwargs.get("ContentType"),
			"ObjectLockMode": mode,
			"ObjectLockRetainUntilDate": until,
		}
		self.versions.setdefault((Bucket, Key), []).append(version)
		return {"VersionId": version["VersionId"]}

	def head_object(self, Bucket, Key, VersionId=None):
		self.record("head_object", Bucket=Bucket, Key=Key)
		version = self.latest(Bucket, Key, VersionId)
		return {
			"ContentLength": len(version["Body"]),
			"Metadata": version["Metadata"],
			"VersionId": version["VersionId"],
			"ObjectLockMode": version["ObjectLockMode"],
			"ObjectLockRetainUntilDate": version["ObjectLockRetainUntilDate"],
		}

	def get_object(self, Bucket, Key, VersionId=None):
		self.record("get_object", Bucket=Bucket, Key=Key)
		version = self.latest(Bucket, Key, VersionId, "GetObject")
		return {"Body": io.BytesIO(version["Body"]), "VersionId": version["VersionId"]}

	def get_object_retention(self, Bucket, Key, VersionId=None):
		self.record("get_object_retention", Bucket=Bucket, Key=Key)
		version = self.latest(Bucket, Key, VersionId, "GetObjectRetention")
		return {
			"Retention": {
				"Mode": version["ObjectLockMode"],
				"RetainUntilDate": version["ObjectLockRetainUntilDate"],
			}
		}

	def put_object_retention(self, Bucket, Key, Retention, VersionId=None, BypassGovernanceRetention=False):
		self.record("put_object_retention", Bucket=Bucket, Key=Key, Retention=Retention, VersionId=VersionId)
		version = self.latest(Bucket, Key, VersionId, "PutObjectRetention")
		if Retention["Mode"] != version["ObjectLockMode"] and not (
			BypassGovernanceRetention and version["ObjectLockMode"] == "GOVERNANCE"
		):
			raise error("AccessDenied", "PutObjectRetention")
		if Retention["RetainUntilDate"] < version["ObjectLockRetainUntilDate"]:
			raise error("AccessDenied", "PutObjectRetention")
		version["ObjectLockMode"] = Retention["Mode"]
		version["ObjectLockRetainUntilDate"] = Retention["RetainUntilDate"]
		return {}

	def delete_object(self, Bucket, Key, VersionId=None):
		self.record("delete_object", Bucket=Bucket, Key=Key, VersionId=VersionId)
		if VersionId is None:
			self.versions.setdefault((Bucket, Key), [])
			return {"DeleteMarker": True}
		version = self.latest(Bucket, Key, VersionId, "DeleteObject")
		if version["ObjectLockRetainUntilDate"] and version["ObjectLockRetainUntilDate"] > datetime.now(UTC):
			raise error("AccessDenied", "DeleteObject")
		self.versions[(Bucket, Key)].remove(version)
		return {}

	def body(self, Bucket, Key):
		return self.latest(Bucket, Key)["Body"]
