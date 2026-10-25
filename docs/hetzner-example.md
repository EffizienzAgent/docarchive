# Example: Hetzner Object Storage

This is one way to set up WORM storage for docarchive. Any S3-compatible provider with Object Lock works. Names, regions and principals below are placeholders.

It is a technical example, not a compliance recipe. See "No compliance promise" in the README.

## 1. Buckets

Create two buckets in two locations, e.g. one in `fsn1` (or `nbg1`) and one in `hel1`:

- `example-archive-fsn1`
- `example-archive-hel1`

Rules:

- **Enable Object Lock when you create the bucket.** It cannot be added later. With Object Lock, versioning is always on.
- **Set no default retention** on the bucket. docarchive sets mode and retain-until date on each object at upload.
- Use one bucket pair per source app. Access keys are project-wide, so separate buckets keep one writer from reading or listing another app's archive.
- For first tests, create a separate test bucket (e.g. `example-archive-test`) and use only GOVERNANCE with a 1-day retention there. A COMPLIANCE object pins its bucket until its retention ends.

## 2. Keys

Use two keys, optionally three:

- An **admin key** that you keep offline. It manages policies and does the one-time switch to COMPLIANCE.
- A **writer key** for the Frappe site. In the archive buckets it may put objects, extend retention, read and list. It may never delete, shorten retention, bypass governance, or change policy, lock or versioning settings.
- Optionally an **archive admin key** for the Frappe site (Archive Admin role). It may read, list, set and release legal holds on existing versions, and delete versions whose retention has ended, and, if you allow `s3:BypassGovernanceRetention`, GOVERNANCE versions before then. It may never upload, change retention or change bucket settings. No key can delete a COMPLIANCE version before its date.

All other keys in the project get no access at all.

## 3. Bucket policy

Ceph-based storage enforces `Deny` statements with an `Action` list and `NotPrincipal`. `NotAction` is accepted but ignored, so always list actions explicitly. **Every statement must exclude the admin key.** A policy that misses it can lock the admin key out of the bucket, and undoing that needs the provider's support.

Name every key other than the admin key in `Principal`, never in `NotPrincipal`: a `NotPrincipal` list that leaves out the admin key catches it too. The example below names only the admin key in `NotPrincipal`, which works as long as there is no third key; with an archive admin key, use the script below.

Replace `ADMIN_PRINCIPAL` and `WRITER_PRINCIPAL` with the principal format from your provider's documentation. Apply the policy to both buckets (change the resource names).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OnlyAdminAndWriter",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["ADMIN_PRINCIPAL", "WRITER_PRINCIPAL"] },
      "Action": ["s3:*"],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    },
    {
      "Sid": "WriterCannotDestroy",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["ADMIN_PRINCIPAL"] },
      "Action": [
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:DeleteBucket",
        "s3:BypassGovernanceRetention",
        "s3:PutBucketPolicy",
        "s3:DeleteBucketPolicy",
        "s3:PutBucketObjectLockConfiguration",
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
        "s3:PutObjectLegalHold"
      ],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    }
  ]
}
```

The writer keeps `s3:PutObject`, `s3:PutObjectRetention`, `s3:GetObject`, `s3:GetObjectVersion`, `s3:GetObjectRetention` and `s3:ListBucket`. Object Lock itself makes `PutObjectRetention` extend-only. Without `s3:BypassGovernanceRetention`, the writer cannot shorten or remove GOVERNANCE retention either. Policies take effect after a few seconds.

### Setting the policy with a script

`scripts/apply_bucket_policy.py` builds the same kind of policy for one bucket, with an optional archive admin key, and prints it. With `--apply` it sets it and then reads it back. `--profile` must be the admin key. The script refuses when that key's access key ID is not part of `--admin-principal`, so it cannot lock the admin key out. Run it once per bucket. It needs [uv](https://docs.astral.sh/uv/), which installs boto3 for the script.

```
uv run scripts/apply_bucket_policy.py --profile admin \
  --endpoint https://fsn1.your-objectstorage.com --region fsn1 --bucket example-archive-fsn1 \
  --admin-principal ADMIN_PRINCIPAL --writer-principal WRITER_PRINCIPAL \
  --archive-admin-principal ARCHIVE_ADMIN_PRINCIPAL
```

- `--writer-legal-hold deny` (default) keeps the writer away from legal holds. `on-only` lets it set a hold but never release it. Ceph does not fill the condition key `s3:object-lock-legal-hold` for `PutObjectLegalHold`, so on Hetzner the writer can then only set the hold at upload (`ObjectLockLegalHoldStatus=ON` on `PutObject`). The policy fails closed: an unsupported condition key never lets the writer release a hold.
- `--archive-admin-bypass denied` also takes `s3:BypassGovernanceRetention` from the archive admin.

Run the check script (section 5) after every policy change.

### Legal holds on Hetzner

S3 has one permission, `s3:PutObjectLegalHold`, for setting and releasing a hold on an existing version. Ceph RGW does not fill the condition key `s3:object-lock-legal-hold`, so an ON-only rule for the writer never matches and the policy fails closed (tested on fsn1). docarchive therefore uses:

- the writer key only for a hold at upload (`PutObject` with `ObjectLockLegalHoldStatus=ON`): **Set Legal Hold When Archiving** on a document, or `legal_hold=true` in the upload API. It covers the main file, every attachment, the `.meta.json` and later extension sidecars, and is checked with a versioned HEAD. It works without an archive admin key;
- the archive admin key for every change of an existing version, ON and OFF: **Set Legal Hold** and **Release Legal Hold** on archived documents. Without that key both are hidden and refused.

Use `--legal-hold upload` in the check script below to test this behaviour.

## 4. Site configuration

Put the bucket locations and the writer key into the site config, for example:

```
bench --site <site> set-config s3_archive_primary_endpoint https://fsn1.your-objectstorage.com
bench --site <site> set-config s3_archive_primary_region fsn1
bench --site <site> set-config s3_archive_primary_bucket example-archive-fsn1
bench --site <site> set-config s3_archive_secondary_endpoint https://hel1.your-objectstorage.com
bench --site <site> set-config s3_archive_secondary_region hel1
bench --site <site> set-config s3_archive_secondary_bucket example-archive-hel1
bench --site <site> set-config s3_archive_access_key <writer access key>
bench --site <site> set-config s3_archive_secret_key <writer secret key>
```

Then add two rows under **Archive Settings → Storage Targets**:

| Target Name | Config Source | Site Config Prefix | Access Key Config Key | Secret Key Config Key | Lock Mode |
|---|---|---|---|---|---|
| `primary` | Site Config | `s3_archive_primary` | `s3_archive_access_key` | `s3_archive_secret_key` | GOVERNANCE |
| `secondary` | Site Config | `s3_archive_secondary` | `s3_archive_access_key` | `s3_archive_secret_key` | GOVERNANCE |

With an archive admin key, add it to the site config and name it in the **Admin Key** section of both rows (**Admin Access Key Config Key** `s3_archive_admin_access_key`, **Admin Secret Key Config Key** `s3_archive_admin_secret_key`):

```
bench --site <site> set-config s3_archive_admin_access_key <archive admin access key>
bench --site <site> set-config s3_archive_admin_secret_key <archive admin secret key>
```

As long as these values are missing, setting a hold on archived documents, releasing holds and deleting documents are unavailable; archiving and the hold at upload keep working. Never put the offline admin key into the site.

## 5. Check routine

`scripts/check_object_lock.py` runs the key checks against one bucket: the writer may upload with retention, extend it and read, but never delete, shorten, bypass governance or change the bucket. With an archive admin key it also checks that key's limits, a short-lived COMPLIANCE version (90 seconds) and legal holds, and cleans up its `_check/` objects. Run it once per bucket, first against the test bucket:

```
uv run scripts/check_object_lock.py --writer-profile writer --admin-profile archive-admin \
  --endpoint https://fsn1.your-objectstorage.com --region fsn1 --bucket example-archive-fsn1 \
  --legal-hold upload
```

Keys can also come from `WRITER_ACCESS_KEY_ID`/`WRITER_SECRET_ACCESS_KEY` and `ADMIN_ACCESS_KEY_ID`/`ADMIN_SECRET_ACCESS_KEY`. `--legal-hold` says what the writer may do: `off` (default, skip the checks), `upload` (set a hold at upload, the Hetzner behaviour with `on-only`) or `api` (also with `PutObjectLegalHold`). It exits with status 1 if any check fails. Without an archive admin key the check objects stay: the locked ones until their short retention ends, the unlocked one until the admin key deletes it.

The same checks by hand with the AWS CLI, first against the test bucket, then against each archive bucket. `--profile writer` and `--profile admin` hold the two keys, and `--endpoint-url` points at the bucket's location.

```
# Upload with a short GOVERNANCE lock
aws s3api put-object --profile writer --bucket example-archive-test --key check/probe.txt \
  --body probe.txt --content-md5 "$(openssl md5 -binary probe.txt | base64)" \
  --object-lock-mode GOVERNANCE --object-lock-retain-until-date "$(date -u -d '+1 day' +%FT%TZ)"

# Mode and date are stored
aws s3api head-object --profile writer --bucket example-archive-test --key check/probe.txt

# The writer cannot delete a version, shorten retention or bypass governance (all must fail)
aws s3api delete-object --profile writer --bucket example-archive-test --key check/probe.txt --version-id <id>
aws s3api put-object-retention --profile writer --bucket example-archive-test --key check/probe.txt \
  --retention Mode=GOVERNANCE,RetainUntilDate=<earlier date>

# Extending works
aws s3api put-object-retention --profile writer --bucket example-archive-test --key check/probe.txt \
  --retention Mode=GOVERNANCE,RetainUntilDate=<later date>

# The bytes match
aws s3api get-object --profile writer --bucket example-archive-test --key check/probe.txt out.txt
sha256sum probe.txt out.txt
```

The provider may accept `--checksum-algorithm SHA256` without storing or returning the checksum. That is why docarchive computes the SHA-256 itself and records it with the document.

Repeat regularly on the real buckets:

- List one own company and year (`aws s3api list-objects-v2 --prefix <company>/<year>/`) and compare the count with the archived documents in the site.
- Pick some objects, download them, and compare their SHA-256 with `archive_sha256` in the site.
- `head-object` on a few objects: is the mode and retain-until date what you expect?
- Repeat for the second bucket.

## 6. GOVERNANCE first, then COMPLIANCE

1. Run a trial in **GOVERNANCE**, but with the full retention date. Then switching later changes only the mode.
2. Check the trial objects with the routine above, in both buckets.
3. When you are satisfied, set **Lock Mode = COMPLIANCE** on each storage target. From then on, new uploads use COMPLIANCE.
4. Switch existing objects by hand with the **admin key**. Keep the same retain-until date for every version of each document and its `.meta.json`:

   ```
   aws s3api put-object-retention --profile admin --bypass-governance-retention \
     --bucket example-archive-fsn1 --key <key> --version-id <id> \
     --retention Mode=COMPLIANCE,RetainUntilDate=<same date>
   ```

5. Verify with `head-object` on both buckets.
6. Run `bench --site <site> docarchive-sync-mode --target <name>` for each target, so the site records the new mode.

**COMPLIANCE cannot be undone.** No key, not even the admin key, can delete a COMPLIANCE object or shorten its retention before the date. A delete without a version ID only adds a delete marker. Never use COMPLIANCE in the test bucket.

## 7. Lifecycle rule for the audit log

docarchive stores each audit log entry as a locked object under `_audit/` and never deletes it. To remove entries after their lock has ended, set a lifecycle rule on each archive bucket with the offline admin key. Limit it to the `_audit/` prefix, so it can never touch archived documents.

An entry is locked until January 1 after its year plus the configured years (default 10), so at most about 11 years. In a bucket with Object Lock, expiring an object only adds a delete marker; the locked version itself is removed by the noncurrent version rule once its lock has ended. With a default of 10 years:

```json
{
  "Rules": [
    {
      "ID": "docarchive-audit-expiry",
      "Status": "Enabled",
      "Filter": { "Prefix": "_audit/" },
      "Expiration": { "Days": 4030 },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 30 }
    },
    {
      "ID": "docarchive-audit-markers",
      "Status": "Enabled",
      "Filter": { "Prefix": "_audit/" },
      "Expiration": { "ExpiredObjectDeleteMarker": true }
    }
  ]
}
```

```
aws s3api put-bucket-lifecycle-configuration --profile admin \
  --bucket example-archive-fsn1 --lifecycle-configuration file://audit-lifecycle.json
```

Raise `Days` when you raise **Lock Audit Log For (Years)**. Providers differ in which lifecycle actions they support and how they treat locked versions (some skip them, some retry later). Check yours before relying on the rule, e.g. with a short-lived test prefix in the test bucket. The writer key keeps its deny on `s3:PutLifecycleConfiguration`.

