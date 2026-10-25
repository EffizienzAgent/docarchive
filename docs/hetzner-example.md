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

Use three keys:

- An **owner key** that you keep offline. It manages policies and does the one-time switch to COMPLIANCE. It is never stored in the site.
- A **writer key** (the archive key) for the Frappe site. In the archive buckets it may put objects (with a legal hold at upload), extend retention, read and list. It may never delete, shorten retention, bypass governance, change the legal hold of an existing version, or change policy, lock or versioning settings.
- An optional **admin key** for the Frappe site. It may set and release legal holds on existing versions, delete object versions and, if you want early deletion, bypass GOVERNANCE retention. It may read and list, but never put objects, delete without a version ID (delete markers), change retention or change policy, lock or versioning settings. Without it, the site can still archive, and set a hold at upload time, but cannot set or release holds on archived documents or delete documents.

All other keys in the project get no access at all.

## 3. Bucket policy

Ceph-based storage enforces `Deny` statements with an `Action` list and `NotPrincipal`. `NotAction` is accepted but ignored, so always list actions explicitly. **Every statement must exclude the owner key.** A policy that misses it can lock the owner key out of the bucket, and undoing that needs the provider's support.

Replace `OWNER_PRINCIPAL`, `WRITER_PRINCIPAL` and `ADMIN_PRINCIPAL` with the principal format from your provider's documentation. Apply the policy to both buckets (change the resource names). Leave out `ADMIN_PRINCIPAL` until you create the admin key.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "OnlyOwnerWriterAdmin",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["OWNER_PRINCIPAL", "WRITER_PRINCIPAL", "ADMIN_PRINCIPAL"] },
      "Action": ["s3:*"],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    },
    {
      "Sid": "OnlyOwnerChangesTheBucket",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["OWNER_PRINCIPAL"] },
      "Action": [
        "s3:DeleteObject",
        "s3:DeleteBucket",
        "s3:PutBucketPolicy",
        "s3:DeleteBucketPolicy",
        "s3:PutBucketObjectLockConfiguration",
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
        "s3:PutObjectTagging",
        "s3:DeleteObjectTagging"
      ],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    },
    {
      "Sid": "WriterCannotDestroy",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["OWNER_PRINCIPAL", "ADMIN_PRINCIPAL"] },
      "Action": ["s3:DeleteObjectVersion", "s3:BypassGovernanceRetention"],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    },
    {
      "Sid": "AdminCannotWrite",
      "Effect": "Deny",
      "NotPrincipal": { "AWS": ["OWNER_PRINCIPAL", "WRITER_PRINCIPAL"] },
      "Action": ["s3:PutObject", "s3:PutObjectRetention"],
      "Resource": ["arn:aws:s3:::example-archive-fsn1", "arn:aws:s3:::example-archive-fsn1/*"]
    },
    {
      "Sid": "WriterCannotChangeHolds",
      "Effect": "Deny",
      "Principal": { "AWS": ["WRITER_PRINCIPAL"] },
      "Action": ["s3:PutObjectLegalHold"],
      "Resource": ["arn:aws:s3:::example-archive-fsn1/*"]
    }
  ]
}
```

The writer keeps `s3:PutObject` (which includes `ObjectLockLegalHoldStatus=ON` at upload), `s3:PutObjectRetention`, `s3:GetObject`, `s3:GetObjectVersion`, `s3:GetObjectRetention`, `s3:GetObjectLegalHold` and `s3:ListBucket`. Object Lock itself makes `PutObjectRetention` extend-only. Without `s3:BypassGovernanceRetention`, the writer cannot shorten or remove GOVERNANCE retention either. The admin key keeps `s3:PutObjectLegalHold`, `s3:DeleteObjectVersion`, `s3:BypassGovernanceRetention` and the read and list actions. Drop `s3:BypassGovernanceRetention` from its allowed actions (add it to the `AdminCannotWrite` statement) if you do not want early deletion; the site then reports the AccessDenied on the copy. Policies take effect after a few seconds.

**Provider limitation (tested on Hetzner fsn1):** S3 has one permission, `s3:PutObjectLegalHold`, for setting and releasing a hold on an existing version. AWS can limit it to ON with the `s3:object-lock-legal-hold` condition, but Ceph RGW does not set that condition key, so an ON-only allow never matches. The policy above therefore denies the writer `PutObjectLegalHold` completely. What works: the writer sets a hold while uploading (`PutObject` with `ObjectLockLegalHoldStatus=ON`, used by **Set Legal Hold When Archiving**), and only the admin key changes the hold of an existing version, ON or OFF. Object Lock still refuses any delete of a held or locked version.

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

When you create the admin key, add it too:

```
bench --site <site> set-config s3_archive_admin_access_key <admin access key>
bench --site <site> set-config s3_archive_admin_secret_key <admin secret key>
```

Then add two rows under **Archive Settings → Storage Targets**:

| Target Name | Config Source | Site Config Prefix | Access Key Config Key | Secret Key Config Key | Lock Mode |
|---|---|---|---|---|---|
| `primary` | Site Config | `s3_archive_primary` | `s3_archive_access_key` | `s3_archive_secret_key` | GOVERNANCE |
| `secondary` | Site Config | `s3_archive_secondary` | `s3_archive_access_key` | `s3_archive_secret_key` | GOVERNANCE |

In the **Admin Key** section of both rows, set **Admin Access Key Config Key** to `s3_archive_admin_access_key` and **Admin Secret Key Config Key** to `s3_archive_admin_secret_key`. As long as these site config values are missing, releasing holds and deleting documents are unavailable.

## 5. Check routine

Run these checks with the AWS CLI against the test bucket first, then against each archive bucket. `--profile writer`, `--profile admin` and `--profile owner` hold the keys, and `--endpoint-url` points at the bucket's location.

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

# The writer can set a hold only at upload, not on an existing version (the second must fail)
aws s3api put-object --profile writer --bucket example-archive-test --key check/held.txt \
  --body probe.txt --content-md5 "$(openssl md5 -binary probe.txt | base64)" \
  --object-lock-mode GOVERNANCE --object-lock-retain-until-date "$(date -u -d '+1 day' +%FT%TZ)" \
  --object-lock-legal-hold-status ON
aws s3api put-object-legal-hold --profile writer --bucket example-archive-test --key check/probe.txt \
  --version-id <id> --legal-hold Status=ON

# The admin key sets and releases holds, but cannot write or add delete markers (the last two must fail)
aws s3api put-object-legal-hold --profile admin --bucket example-archive-test --key check/probe.txt \
  --version-id <id> --legal-hold Status=ON
aws s3api put-object-legal-hold --profile admin --bucket example-archive-test --key check/probe.txt \
  --version-id <id> --legal-hold Status=OFF
aws s3api put-object --profile admin --bucket example-archive-test --key check/other.txt --body probe.txt
aws s3api delete-object --profile admin --bucket example-archive-test --key check/probe.txt

# A locked version cannot be deleted by the admin key without bypass (must fail);
# with bypass it can, in GOVERNANCE only (only if you allow early deletion)
aws s3api delete-object --profile admin --bucket example-archive-test --key check/probe.txt --version-id <id>
aws s3api delete-object --profile admin --bypass-governance-retention \
  --bucket example-archive-test --key check/probe.txt --version-id <id>

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
4. Switch existing objects by hand with the **owner key**. Keep the same retain-until date for every version of each document and its `.meta.json`:

   ```
   aws s3api put-object-retention --profile owner --bypass-governance-retention \
     --bucket example-archive-fsn1 --key <key> --version-id <id> \
     --retention Mode=COMPLIANCE,RetainUntilDate=<same date>
   ```

5. Verify with `head-object` on both buckets.
6. Run `bench --site <site> docarchive-sync-mode --target <name>` for each target, so the site records the new mode.

**COMPLIANCE cannot be undone.** No key, not even the owner key, can delete a COMPLIANCE object or shorten its retention before the date, so early deletion in the site is never possible for it. A delete without a version ID only adds a delete marker. Never use COMPLIANCE in the test bucket.
