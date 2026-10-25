# Storage targets

Archive Settings has a **Storage Targets** table with one row per bucket (usually two, in two regions). Each row has a name, an enabled flag and a lock mode (GOVERNANCE or COMPLIANCE). Endpoint, region and bucket come either:

- from the site config (`<prefix>_endpoint`, `<prefix>_region`, `<prefix>_bucket`), with the credentials in two named site config keys, or
- from fields on the row, with the credentials in Password fields, which Frappe stores encrypted.

Credentials are never stored in plain text. Endpoints must use https, must not be private, loopback or link-local addresses or local host names, and bucket names must be valid, for site config values once they are set too. Two targets cannot point to the same endpoint and bucket. The archive can only be enabled with at least one enabled target, and only when all its site config keys are set.

Each row can also name an optional **archive admin key**: in site config mode two more config key names (**Admin Access Key Config Key**, **Admin Secret Key Config Key**, e.g. `s3_archive_admin_access_key` and `s3_archive_admin_secret_key`), in fields mode two more Password fields. Both or neither must be set. Archiving never uses the archive admin key. It is only used to release legal holds and to delete documents. When it is not configured, or its site config keys are empty, those actions are hidden and refused, and everything else keeps working.

## Two keys

| | Archive key (`access_key`) | Archive admin key (`admin_access_key`) |
|---|---|---|
| Used for | archiving (optionally with a legal hold at upload), extending retention, smoke check | setting and releasing legal holds on archived objects, deleting expired documents, early GOVERNANCE delete |
| Needs | `PutObject` (with `ObjectLockLegalHoldStatus` for a hold at upload), `PutObjectRetention`, `GetObject*`, `ListBucket` | `PutObjectLegalHold`, `DeleteObjectVersion`, `GetObject*`, `ListBucket`; `BypassGovernanceRetention` only for early deletion |
| Never | delete, shorten retention, bypass governance, change policies | `PutObject`, `DeleteObject` without version (delete markers), `PutObjectRetention`, policy or lock changes |

S3 has a single permission, `s3:PutObjectLegalHold`, for setting and releasing a hold on an existing version, and providers such as Ceph RGW (Hetzner) do not evaluate the `s3:object-lock-legal-hold` condition that could limit a key to ON. So only the archive admin key changes holds on archived objects, in both directions. The archive key can only set a hold while it uploads a new version (`PutObject` with `ObjectLockLegalHoldStatus=ON`); the AST guard keeps `put_object_legal_hold` away from the archive key.

Any S3-compatible provider with Object Lock works. [docs/hetzner-example.md](hetzner-example.md) shows one setup. `scripts/apply_bucket_policy.py` sets a deny-only bucket policy for the keys, and `scripts/check_object_lock.py` checks what each key can and cannot do in one bucket.
