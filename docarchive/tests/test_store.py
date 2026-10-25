import ast
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import store
from docarchive.tests.fake_s3 import AdminClient, LockedFakeS3

APP_DIR = Path(store.__file__).resolve().parent
CLIENT_CALLS = {
	("_client", "put_object"): "put_locked",
	("_client", "head_object"): "head",
	("_client", "get_object"): "read_sha256",
	("_client", "get_object_retention"): "retention",
	("_client", "delete_object"): "smoke_delete",
	("_client", "put_object_retention"): "extend_locked",
	("_admin_client", "put_object_legal_hold"): "_admin_hold",
	("_admin_client", "delete_object"): "_admin_delete",
}
GUARDED_NAMES = {
	"BypassGovernanceRetention": {"smoke_delete", "delete_early"},
	"_admin_delete": {"_admin_delete", "delete_expired", "delete_early"},
	"_admin_hold": {"_admin_hold", "set_hold", "release_hold"},
}
CLIENTS = {client for client, _method in CLIENT_CALLS}
CLIENT_OWNERS = {
	"_client": {"__init__", *(f for (client, _m), f in CLIENT_CALLS.items() if client == "_client")},
	"_admin_client": {"__init__", "has_admin_access", "_admin_hold", "_admin_delete"},
}
ALLOWED = dict(GUARDED_NAMES)
for (_client, method), function in CLIENT_CALLS.items():
	ALLOWED.setdefault(method, set()).add(function)
BANNED = {
	"delete_objects",
	"copy_object",
	"upload_file",
	"upload_fileobj",
	"download_file",
	"download_fileobj",
	"create_multipart_upload",
	"upload_part",
	"upload_part_copy",
	"complete_multipart_upload",
	"generate_presigned_url",
	"generate_presigned_post",
	"put_object_lock_configuration",
	"put_bucket_policy",
	"put_bucket_versioning",
	"put_bucket_lifecycle_configuration",
	"delete_bucket",
}
CLIENT_MODULES = ("boto3", "botocore", "s3storage.client")


def future(days=1):
	return datetime.now(UTC) + timedelta(days=days)


def fake_target(name="primary", fake=None, lock_mode="GOVERNANCE", admin=False):
	fake = fake or LockedFakeS3()
	return store.Target(name, f"archive-{name}", lock_mode, fake, AdminClient(fake) if admin else None)


def app_modules():
	for path in APP_DIR.rglob("*.py"):
		relative = path.relative_to(APP_DIR)
		if "tests" not in relative.parts:
			yield relative.as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def names_in(node):
	if isinstance(node, ast.Attribute):
		yield node.attr
	elif isinstance(node, ast.Constant) and isinstance(node.value, str):
		yield node.value
	elif isinstance(node, ast.keyword) and node.arg:
		yield node.arg
	elif isinstance(node, ast.Name):
		yield node.id
	elif isinstance(node, ast.Import):
		yield from (f"import:{alias.name}" for alias in node.names)
	elif isinstance(node, ast.ImportFrom) and node.module:
		yield f"import:{node.module}"
		yield from (f"import:{node.module}.{alias.name}" for alias in node.names)
		yield from (alias.name for alias in node.names)


def client_call(node):
	if (
		isinstance(node, ast.Attribute)
		and isinstance(node.value, ast.Attribute)
		and node.value.attr in CLIENTS
	):
		return (node.value.attr, node.attr)
	return None


def violations(modules):
	found = []
	for path, tree in modules:
		functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
		for node in ast.walk(tree):
			line = getattr(node, "lineno", 0)
			owner = next((f.name for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)), None)
			call = client_call(node)
			if call and (path, owner) != ("store.py", CLIENT_CALLS.get(call)):
				found.append((path, owner, ".".join(call)))
			for name in names_in(node):
				module = name.removeprefix("import:")
				if name in BANNED:
					found.append((path, owner, name))
				elif name in ALLOWED and (path != "store.py" or owner not in ALLOWED[name]):
					found.append((path, owner, name))
				elif name in CLIENTS and (path != "store.py" or owner not in CLIENT_OWNERS[name]):
					found.append((path, owner, name))
				elif name == "_client_for" and path != "store.py":
					found.append((path, owner, name))
				elif name.startswith("import:") and module.startswith(CLIENT_MODULES) and path != "store.py":
					found.append((path, owner, name))
	return found


class TestStore(IntegrationTestCase):
	def test_put_locked_sends_lock_headers_md5_and_sha256(self):
		target = fake_target()
		until = future(30)
		result = store.put_locked(target, "a/b.pdf", b"%PDF-data", "GOVERNANCE", until, "application/pdf")
		[put] = target._client.puts()
		self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
		self.assertEqual(put["ObjectLockRetainUntilDate"], until)
		self.assertEqual(put["ContentMD5"], base64.b64encode(hashlib.md5(b"%PDF-data").digest()).decode())
		self.assertEqual(put["Metadata"], {"sha256": hashlib.sha256(b"%PDF-data").hexdigest()})
		self.assertEqual(
			(put["Bucket"], put["Key"], put["ContentType"]), ("archive-primary", "a/b.pdf", "application/pdf")
		)
		self.assertEqual(result["sha256"], put["Metadata"]["sha256"])
		self.assertTrue(result["version_id"])

	def test_compliance_mode_accepted(self):
		target = fake_target()
		store.put_locked(target, "k", b"x", "COMPLIANCE", future())
		self.assertEqual(target._client.puts()[0]["ObjectLockMode"], "COMPLIANCE")

	def test_retain_until_converted_to_utc(self):
		target = fake_target()
		local = (datetime.now(UTC) + timedelta(days=2)).astimezone(ZoneInfo("Europe/Berlin"))
		store.put_locked(target, "k", b"x", "GOVERNANCE", local.isoformat())
		sent = target._client.puts()[0]["ObjectLockRetainUntilDate"]
		self.assertEqual((sent, sent.utcoffset()), (local, timedelta(0)))

	def test_invalid_lock_makes_no_call(self):
		naive = datetime.now() + timedelta(days=1)
		cases = {
			"missing date": ("GOVERNANCE", None),
			"past date": ("GOVERNANCE", datetime.now(UTC) - timedelta(seconds=1)),
			"now": ("GOVERNANCE", datetime.now(UTC)),
			"naive date": ("GOVERNANCE", naive),
			"naive string": ("GOVERNANCE", naive.isoformat()),
			"unparseable": ("GOVERNANCE", "next year"),
			"date only": ("GOVERNANCE", future().date()),
			"no mode": (None, future()),
			"lowercase mode": ("governance", future()),
			"unknown mode": ("LEGAL_HOLD", future()),
		}
		for case, (mode, until) in cases.items():
			target = fake_target()
			with self.subTest(case), self.assertRaises(store.LockError):
				store.put_locked(target, "k", b"x", mode, until)
			self.assertEqual(target._client.calls, [])

	def test_put_locked_needs_key_and_bytes(self):
		target = fake_target()
		for key, body in (("", b"x"), ("k", "text")):
			with self.subTest(key=key), self.assertRaises(store.LockError):
				store.put_locked(target, key, body, "GOVERNANCE", future())
		self.assertEqual(target._client.calls, [])

	def test_head_returns_none_for_missing_object(self):
		target = fake_target()
		self.assertIsNone(store.head(target, "missing"))
		store.put_locked(target, "k", b"x", "GOVERNANCE", future())
		self.assertEqual(store.head(target, "k")["Metadata"]["sha256"], hashlib.sha256(b"x").hexdigest())

	def test_head_raises_other_errors(self):
		target = fake_target(fake=LockedFakeS3(fail={"head_object"}))
		with self.assertRaises(Exception):
			store.head(target, "k")

	def test_read_sha256_hashes_the_stored_bytes(self):
		target = fake_target()
		store.put_locked(target, "k", b"stored bytes", "GOVERNANCE", future())
		version = store.head(target, "k")["VersionId"]
		self.assertEqual(store.read_sha256(target, "k", version), hashlib.sha256(b"stored bytes").hexdigest())
		with self.assertRaises(store.LockError):
			store.read_sha256(target, "k", None)

	def test_smoke_delete_only_for_smoke_objects(self):
		target = fake_target()
		smoke_key = "_smoke/20261025T010203Z-0123abcd.txt"
		for key, version in (
			("doc.pdf", "v1"),
			("_smoke/../doc.pdf", "v1"),
			("_smoke/anything.txt", "v1"),
			(smoke_key + ".pdf", "v1"),
			(smoke_key, None),
			(smoke_key, ""),
		):
			with self.subTest(key=key, version=version), self.assertRaises(store.LockError):
				store.smoke_delete(target, key, version)
		self.assertNotIn("delete_object", [call[0] for call in target._client.calls])

	def test_only_lock_errors_count_as_refused(self):
		from docarchive.tests.fake_s3 import error

		self.assertTrue(store.refused(error("AccessDenied", "DeleteObject")))
		self.assertFalse(store.refused(error("NoSuchKey", "DeleteObject")))
		self.assertFalse(store.refused(error("InvalidRequest", "DeleteObject")))
		self.assertFalse(store.refused(RuntimeError("network")))

	def test_verify_checks_mode_date_and_sha(self):
		target = fake_target()
		until = future(30)
		version = store.put_locked(target, "k", b"x", "GOVERNANCE", until)["version_id"]
		sha = hashlib.sha256(b"x").hexdigest()
		store.verify(target, "k", version, sha, "GOVERNANCE", until)
		cases = {
			"mode": ("COMPLIANCE", until, sha),
			"later date": ("GOVERNANCE", until + timedelta(days=1), sha),
			"sha": ("GOVERNANCE", until, "0" * 64),
		}
		for case, (mode, date, digest) in cases.items():
			with self.subTest(case), self.assertRaises(store.VerifyError):
				store.verify(target, "k", version, digest, mode, date)

	def test_store_has_no_general_delete(self):
		self.assertEqual(
			[name for name in dir(store) if "delete" in name.lower()],
			["_admin_delete", "delete_early", "delete_expired", "smoke_delete"],
		)

	def test_only_store_reaches_the_raw_client(self):
		modules = list(app_modules())
		self.assertIn("store.py", dict(modules))
		self.assertEqual(violations(modules), [])

	def test_guard_detects_other_callers(self):
		samples = {
			"put elsewhere": (
				"run.py",
				"def sneaky(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
			),
			"getattr": ("api.py", "def sneaky(c):\n    getattr(c, 'put_object')()\n"),
			"wrong function": (
				"store.py",
				"def head(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
			),
			"head elsewhere": (
				"store.py",
				"def put_locked(t):\n    t._client.head_object(Bucket='b', Key='k')\n",
			),
			"copy": ("store.py", "def put_locked(t):\n    t._client.copy_object(Bucket='b')\n"),
			"multipart": (
				"store.py",
				"def put_locked(t):\n    t._client.create_multipart_upload(Bucket='b')\n",
			),
			"presign": ("store.py", "def head(t):\n    t._client.generate_presigned_url('get_object')\n"),
			"legal hold": ("store.py", "def head(t):\n    t._client.put_object_legal_hold(Bucket='b')\n"),
			"bypass": (
				"store.py",
				"def put_locked(t):\n    t._client.put_object(BypassGovernanceRetention=True)\n",
			),
			"retention write": ("store.py", "def retention(t):\n    t._client.put_object_retention()\n"),
			"client import": ("run.py", "from s3storage.client import get_client\n"),
			"client module": ("run.py", "from s3storage import client\n"),
			"boto3": ("api.py", "import boto3\n"),
			"raw client": ("run.py", "def f(t):\n    return t._client\n"),
			"client factory": ("run.py", "def f(c):\n    return store._client_for(c)\n"),
			"unlisted method": ("store.py", "def head(t):\n    t._client.list_object_versions(Bucket='b')\n"),
			"release with archive key": (
				"store.py",
				"def release_hold(t):\n    t._client.put_object_legal_hold(Bucket='b')\n",
			),
			"set with archive key": (
				"store.py",
				"def set_hold(t):\n    t._client.put_object_legal_hold(Bucket='b')\n",
			),
			"admin hold outside helper": (
				"store.py",
				"def set_hold(t):\n    t._admin_client.put_object_legal_hold(Bucket='b')\n",
			),
			"admin hold from run": ("run.py", "def f(t):\n    store._admin_hold(t, 'k', 'v', 'OFF')\n"),
			"admin delete elsewhere": (
				"store.py",
				"def delete_expired(t):\n    t._admin_client.delete_object(Bucket='b')\n",
			),
			"admin put": ("store.py", "def _admin_delete(t):\n    t._admin_client.put_object(Bucket='b')\n"),
			"admin retention": (
				"store.py",
				"def release_hold(t):\n    t._admin_client.put_object_retention(Bucket='b')\n",
			),
			"bypass on expired delete": (
				"store.py",
				"def delete_expired(t):\n    _admin_delete(t, 'k', 'v', BypassGovernanceRetention=True)\n",
			),
			"admin delete from run": ("run.py", "def f(t):\n    store._admin_delete(t, 'k', 'v')\n"),
			"admin client aliased": (
				"store.py",
				"def smoke_delete(t):\n    c = t._admin_client\n    c.delete_object(BypassGovernanceRetention=True)\n",
			),
			"admin client by name": (
				"store.py",
				"def smoke_delete(t):\n    getattr(t, '_admin_client').delete_object(VersionId='v')\n",
			),
			"archive client aliased": (
				"store.py",
				"def deletable(t):\n    c = t._client\n    c.delete_object(BypassGovernanceRetention=True)\n",
			),
			"admin delete imported": ("run.py", "from docarchive.store import _admin_delete\n"),
			"admin client elsewhere": ("run.py", "def f(t):\n    return t._admin_client\n"),
			"hold elsewhere": ("run.py", "def f(t):\n    t._client.put_object_legal_hold(Bucket='b')\n"),
		}
		for case, (path, source) in samples.items():
			with self.subTest(case):
				self.assertTrue(violations([(path, ast.parse(source))]))


class TestHoldAndDelete(IntegrationTestCase):
	def locked(self, admin=True, mode="GOVERNANCE", until=None, **fake):
		target = fake_target(fake=LockedFakeS3(**fake), admin=admin, lock_mode=mode)
		version = store.put_locked(target, "k", b"x", mode, until or future(30))["version_id"]
		return target, version

	def expire(self, target):
		for versions in target._client.versions.values():
			for version in versions:
				version["ObjectLockRetainUntilDate"] = datetime.now(UTC) - timedelta(days=1)

	def test_put_locked_can_set_the_hold_at_upload(self):
		target, _version = self.locked(admin=False)
		held = store.put_locked(target, "h", b"x", "GOVERNANCE", future(30), legal_hold=True)
		self.assertEqual(target._client.puts()[-1]["ObjectLockLegalHoldStatus"], "ON")
		self.assertNotIn("ObjectLockLegalHoldStatus", target._client.puts()[0])
		self.assertEqual(store.hold_status(target, "h", held["version_id"]), "ON")
		sha = hashlib.sha256(b"x").hexdigest()
		store.verify(target, "h", held["version_id"], sha, "GOVERNANCE", future(29), legal_hold=True)
		with self.assertRaises(store.VerifyError):
			store.verify(target, "k", _version, sha, "GOVERNANCE", future(29), legal_hold=True)

	def test_set_hold_uses_the_admin_key_and_verifies(self):
		target, version = self.locked()
		store.set_hold(target, "k", version)
		self.assertEqual(store.hold_status(target, "k", version), "ON")
		[(operation, kwargs)] = target._client.calls_by("admin")
		self.assertEqual(operation, "put_object_legal_hold")
		self.assertEqual((kwargs["VersionId"], kwargs["LegalHold"]), (version, {"Status": "ON"}))
		self.assertNotIn("put_object_legal_hold", [op for op, _kw in target._client.calls_by("archive")])

	def test_set_hold_needs_admin_key_and_version(self):
		plain, plain_version = self.locked(admin=False)
		with self.assertRaises(store.LockError):
			store.set_hold(plain, "k", plain_version)
		target, _version = self.locked()
		with self.assertRaises(store.LockError):
			store.set_hold(target, "k", None)
		self.assertNotIn(
			"put_object_legal_hold", [op for op, _kw in plain._client.calls + target._client.calls]
		)

	def test_release_needs_the_admin_key(self):
		target = fake_target()
		version = store.put_locked(target, "k", b"x", "GOVERNANCE", future(30), legal_hold=True)["version_id"]
		with self.assertRaises(store.LockError):
			store.release_hold(target, "k", version)
		with self.assertRaises(Exception) as caught:
			target._client.put_object_legal_hold(
				Bucket=target.bucket, Key="k", VersionId=version, LegalHold={"Status": "OFF"}
			)
		self.assertEqual(store.error_code(caught.exception), "AccessDenied")
		self.assertEqual(store.hold_status(target, "k", version), "ON")

	def test_release_uses_the_admin_key(self):
		target, version = self.locked()
		store.set_hold(target, "k", version)
		store.release_hold(target, "k", version)
		self.assertEqual(store.hold_status(target, "k", version), "OFF")
		[_set, (operation, kwargs)] = target._client.calls_by("admin")
		self.assertEqual((operation, kwargs["LegalHold"]), ("put_object_legal_hold", {"Status": "OFF"}))

	def test_delete_expired_deletes_only_the_given_version(self):
		target, version = self.locked()
		other = store.put_locked(target, "k", b"y", "GOVERNANCE", future(30))["version_id"]
		self.expire(target)
		target._client.versions[("archive-primary", "k")][1]["ObjectLockRetainUntilDate"] = future(30)
		self.assertTrue(store.delete_expired(target, "k", version))
		self.assertEqual(target._client.deleted(), [("k", version, False)])
		self.assertEqual(target._client.calls_by("admin")[0][0], "delete_object")
		self.assertIsNone(store.head(target, "k", version))
		self.assertEqual(store.head(target, "k", other)["VersionId"], other)
		self.assertFalse(store.delete_expired(target, "k", version))

	def test_delete_expired_refuses_locked_held_and_without_admin(self):
		target, version = self.locked()
		with self.assertRaises(store.LockError):
			store.delete_expired(target, "k", version)
		self.expire(target)
		store.set_hold(target, "k", version)
		with self.assertRaises(store.LockError):
			store.delete_expired(target, "k", version)
		plain, plain_version = self.locked(admin=False)
		self.expire(plain)
		with self.assertRaises(store.LockError):
			store.delete_expired(plain, "k", plain_version)
		for fake in (target._client, plain._client):
			self.assertEqual(fake.deleted(), [])

	def test_delete_expired_lets_storage_refusals_through(self):
		target, version = self.locked()
		self.expire(target)
		target._client.deny = {("admin", "delete_object")}
		with self.assertRaises(Exception) as caught:
			store.delete_expired(target, "k", version)
		self.assertTrue(store.refused(caught.exception))
		self.assertTrue(store.head(target, "k", version))

	def test_delete_early_sends_bypass_only_for_governance(self):
		target, version = self.locked(bypass_allowed=True)
		self.assertTrue(store.delete_early(target, "k", version))
		self.assertEqual(target._client.deleted(), [("k", version, True)])
		compliance, compliance_version = self.locked(mode="COMPLIANCE", bypass_allowed=True)
		with self.assertRaises(store.LockError):
			store.delete_early(compliance, "k", compliance_version)
		held, held_version = self.locked(bypass_allowed=True)
		store.set_hold(held, "k", held_version)
		with self.assertRaises(store.LockError):
			store.delete_early(held, "k", held_version)
		for fake in (compliance._client, held._client):
			self.assertEqual(fake.deleted(), [])

	def test_delete_early_without_bypass_permission_is_refused(self):
		target, version = self.locked()
		with self.assertRaises(Exception) as caught:
			store.delete_early(target, "k", version)
		self.assertTrue(store.refused(caught.exception))
		self.assertTrue(store.head(target, "k", version))

	def test_admin_key_is_optional(self):
		config = frappe._dict(
			name="primary",
			bucket="b",
			lock_mode="GOVERNANCE",
			endpoint="https://s3.example.test",
			region="r",
			access_key="key",
			secret_key="secret",
		)
		with patch("docarchive.store._client_for", side_effect=lambda c: c["access_key"]):
			self.assertFalse(store.has_admin_access(store.from_config(config)))
			config.admin_access_key = "admin"
			self.assertFalse(store.has_admin_access(store.from_config(config)))
			config.admin_secret_key = "admin-secret"
			target = store.from_config(config)
		self.assertTrue(store.has_admin_access(target))
		self.assertEqual((target._client, target._admin_client), ("key", "admin"))
