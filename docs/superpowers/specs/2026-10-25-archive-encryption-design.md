# Client-side encryption of archive targets (issue #42)

## Why

Archived documents stay in WORM buckets for 10 years or more and cannot be moved or rewritten when something goes wrong. Today they sit there in plain text, together with a `.meta.json` that holds title, counterparty, e-mail addresses and the full e-invoice data, and object keys that name the counterparty, the document number or the title. Provider-side encryption does not help: Hetzner Object Storage only offers SSE-C, where the provider sees the key on every request.

Goal is that nobody with access to a bucket, to the provider, to a database dump or, later, to the operator's storage can read the archive, for the whole retention period and also against future quantum computers. This matters most for a possible managed WORM offering, where customer data would sit on the operator's account and "even the operator cannot read your archive" is a strong argument.

The user wants industry standard building blocks only, no self-assembled cryptography.

## Goals

- Opt-in encryption per storage target, off by default. Existing plaintext objects keep working; encrypted and plaintext objects coexist in one target and across targets.
- Each object on an encrypting target is a standard age v1 file, readable with the official `age` CLI and the recovery key, without docarchive, Frappe or the database.
- The server can encrypt but never decrypt on its own. Decryption needs the customer's passphrase (export) or the offline recovery key (emergency).
- Quantum-safe: hybrid post-quantum recipients (`age1pq1…`, ML-KEM-768 + X25519); payload is symmetric.
- Integrity checks (nightly verify, retention extension, mode sync) work without any key.
- The bucket leaks as little as possible: no plaintext hash, no title, number or counterparty in keys or readable metadata.
- Export and export package decrypt and produce the same plaintext output for encrypted, plaintext and mixed archives.
- A Frappe-free recovery path.
- Readability for the whole retention period is provable: keys are checked when registered and recovery is drilled regularly (GoBD, § 147 Abs. 2 AO).

## Non-goals

- The working copy in Frappe's file storage (local or s3storage) and plaintext fields in the database (title, counterparty, e-invoice data). Removing the working copy is #50; client-side encryption of s3storage files is a separate s3storage ticket.
- Re-encrypting existing plaintext objects. They are locked and stay as they are.
- Re-wrapping keys of existing objects (option B below, possibly later).
- Splitting the recovery key (2-of-3). Later.
- Decryption in the browser. Later, to keep the passphrase off the server during exports.
- Signing objects. age does not authenticate the sender; the residual risk is documented (see Recovery).
- Any shared crypto code with s3storage. Both apps solve this independently with the same standard; each works regardless of whether the other encrypts.

## Anti-goals

- Own cryptographic code: no own implementation of age, of envelopes, key derivation or AEAD. Only the official `age` release binaries (`age`, `age-keygen`, `age-plugin-batchpass`, `age-inspect`).
- A design where losing one key makes the archive unreadable, or where a wrong key is only noticed at the audit.
- Locking unreadable data: archiving must refuse to write to an encrypting target when keys are missing, unverified or `age` is not usable, instead of writing something nobody can open.
- The server, the database or a backup holding anything that decrypts on its own.
- The database deciding which program runs or which files get published.

## Constraints

- Object Lock: archived versions are immutable. Anything inside a locked object, including the age header, is permanent.
- `store.py` rules stay: only `put_locked` calls `PutObject`, the archive key never deletes, the admin key never uploads, copy and presigned calls stay banned (`docs/archiving.md`, AST test).
- Exports run up to 6 hours in a background job on the `long` queue and read every object from storage, never from the Frappe file (`docs/export.md`).
- The object key is per document (`doc.object_key`), shared by all targets.
- Hetzner: no default bucket retention, SSE-C only, no copy of SSE-C objects.
- age facts (checked 2026-10-25, latest v1.3.2): native `age1pq1` recipients and `age-keygen -pq` since v1.3.0, specified in C2SP with CCTV test vectors. Release archives contain `age`, `age-keygen`, `age-plugin-batchpass`, `age-inspect`, each with a sigsum `.proof` and GitHub build provenance. batchpass reads `AGE_PASSPHRASE_FD`; scrypt work factor default 18, decryption refuses above 22 unless raised. age runs plugins as `age-plugin-<name>` from `PATH`. pyrage 1.4.0 has no post-quantum recipients, so the app calls the binary.
- GoBD/§ 147 AO (research, not legal advice): records must be readable without delay and machine-evaluable for the whole period; unreadable records count as missing. Encryption is fine because decryption returns the original bytes; the keys must be kept.

## Decisions

### Approach: official age CLI only (decided with the user)

Option A was chosen over B (own age format code on `cryptography` with rotatable envelopes in the database). Reason: industry standard for a critical component, much lower complexity. Accepted consequence: both recipient stanzas sit in the locked header, so a company key that leaks together with its passphrase keeps old objects readable for whoever also has the ciphertext. B stays possible later without migration, because the file format is the same.

### Keys (decided with the user, hardening from review my call)

- Two age key pairs per Own Company, both `age1pq1` (hybrid):
  - **Company key**, for everyday use (export). Public key on the server. Private key stored on the server only as an age file encrypted with the customer's passphrase (scrypt via `age-plugin-batchpass`, work factor 20). The passphrase is never stored.
  - **Recovery key**, for emergencies (passphrase forgotten, database lost, Frappe gone, company key compromised). Public key on the server; private key only offline, never on the server.
- The recovery key adds availability, not confidentiality; it is kept separate so that the everyday key never takes the emergency path with it, one offline copy covers all company key generations, and the later 2-of-3 split only touches the emergency key.
- Same keys for all targets of an Own Company. One passphrase per Own Company for all company key generations.
- No scheduled rotation (user: yearly keys are admin overhead that will be forgotten).
- **Proof of possession before use** (review C1): a recovery key is accepted only after the server age-encrypts a random token to it and the customer pastes back the result of an offline `age -d`. A company key is accepted only after its identity file unlocks with the passphrase and `age-keygen -y` matches the public key. Recipients must match `^age1pq1…$`, and company and recovery key must differ. Encrypt cannot be enabled before this.
- **Set once**: public keys change only through the flows below, each with proof, an audit log entry with key fingerprints and a notification to the Archive Admins:
  - **Change passphrase**: re-encrypts every kept key file in one transaction; each new file is unlocked and checked against its public key before commit.
  - **New company key** (suspected compromise, also after a suspected passphrase leak): new pair for new objects; old objects keep the old stanza; old key files are kept so exports keep working.
  - **New recovery key** (recovery key lost or compromised): for new objects only; the UI says that older objects now depend on the company key alone and asks for an immediate drill with it.
- The **passphrase is generated** (diceware, at least 6 words) and shown once, so it withstands offline guessing against database backups for decades (my call). Uploaded identity files must be exactly one scrypt stanza with work factor ≥ 20 (`age-inspect`).
- **Pinning**: each encrypting target holds a locked `_keys/<n>.json` with the current recipient fingerprints per Own Company. Before each encryption the app compares the recipients from the database with the newest pin and refuses on mismatch; a key flow writes the next pin.
- Each copy records the fingerprint of the company key generation it was encrypted to, so export knows which identity to unlock.
- Key generation: the recovery key is generated by the customer on their own machine (`age-keygen -pq`). The company key can be generated on the server in one step, or locally and uploaded; the docs recommend local generation where the operator must not be able to read.
- Key fields live on **Own Company**; every key event (registration, proof, passphrase change, new key, failed and successful unlocks) is logged in the audit log with fingerprints, never with secrets.

### Custody and drills (from review, my call)

- At least two named key holders per Own Company, a deputy rule, and the recovery key stored in two separate places (e.g. safe and tax advisor), recorded in the desk.
- **Canary**: on enabling Encrypt, and after every key flow, the app writes a locked canary object per target, encrypted like any document.
- **Recovery drill**: the desk offers a drill: the customer decrypts the canary and a few sampled objects with the recovery key and the kit offline and confirms the plaintext hashes. Result is logged. The Archive Admins get a reminder when the last drill is older than 12 months.
- Docs ship a "Keys and custody" section and a template for the Verfahrensdokumentation (which targets encrypt since when, algorithms and age version, key structure and fingerprints, holders and deputies, export/Z1/Z2/Z3 and recovery procedures, drill log, integrity chain, remaining plaintext metadata, handover on sale, liquidation or merger, role split in the managed case).

### Storage target switch (decided with the user, hardening my call)

- Checkbox **Encrypt** on each Archive Storage Target row, off by default. Turning it on only affects copies created afterwards.
- Once a target holds encrypted copies, Encrypt cannot be turned off (review M4); a plaintext target means a new target. The target list shows plaintext and encrypted copy counts.
- Encryption is a property of the copy, fixed when the copy is created: every later write of that copy (pending objects, retries, extension sidecars, legal-hold re-uploads) follows the copy's flag. A copy is never half encrypted.
- Enabling Encrypt is refused, with the reason, when the binaries fail their checks or an enabled Own Company has no verified keys. Enabling it on a target that would receive a backfill of older documents warns with their count: those keep their old, more telling object keys.
- `age` is provided by the Docker image, not embedded in the app. The docs ship a Dockerfile snippet that downloads the official release archives for Linux (amd64, arm64), macOS and Windows, verifies them (checksum and sigsum proof or `gh attestation verify`), puts the server's binaries in a fixed directory and the others in a kit directory. The app never downloads anything at runtime.
- Binary and kit directories come from site config or the environment, never from a DocType (review H1). The app checks the SHA-256 of `age`, `age-keygen`, `age-plugin-batchpass` and `age-inspect` against values pinned in its code before first use, and runs them with a minimal fixed environment (`PATH` = the binary directory only, nothing inherited). Nothing calls `age` while no target encrypts.

### What gets encrypted

- On an encrypting target: main file, attachments, `.meta.json`, extension sidecars. Each is `age -r <recovery> -r <company>` of the plaintext bytes.
- After every encryption the app runs `age-inspect` and requires exactly two `mlkem768x25519` stanzas (review H2).
- Audit log entries stay plaintext (see below).
- Bucket metadata on an encrypted object: an encryption marker (e.g. `enc=age-v1`), the ciphertext SHA-256 in the existing `sha256` field, `Content-Type: application/octet-stream`. Nothing derived from the plaintext. The ciphertext size reveals the plaintext size (documented).
- Object keys (decided with the user): every new document, encrypted or not, gets a minimized key `<own-company-slug>/<year>/<type>/<doc-id>.<ext>`, e.g. `acme/2026/rechnung/DOC-2026-00815.pdf`. The slug is the existing, user-chosen Own Company ID, as today. The docs advise a non-identifying slug for sole traders, whose name is personal data; no new field (decided with the user). Attachments become `<doc-id>__att-<n>.<ext>` without the label; meta and sidecar suffixes stay. No `.age` suffix; the marker is in metadata.
- Already archived documents keep their keys, also when copied to a newly added encrypting target (known limitation, warned and documented). Per-copy keys can follow if moving an existing archive into a managed bucket becomes concrete.
- What a bucket still shows on an encrypting target: company code, year, type, document ID (and with it the yearly count), extension, size, upload time, number of attachments and of retention extensions.

### Hashes, integrity and crash consistency (decided with the user, mechanism my call)

- Plaintext SHA-256 (`content_sha256`, the integrity proof for auditors) stays in the database, the export manifest and inside the encrypted `.meta.json` (which already lists `sha256` per file). It never appears in readable form in the bucket. After decrypting with the recovery key, an auditor can check every file against the locked metadata without Frappe.
- Per encrypted copy the database records the ciphertext SHA-256 of each object. `verify`, `extend_locked`, the nightly checks and `docarchive-sync-mode` compare against it without a key. age's AEAD protects content on decryption; export then checks the plaintext against `content_sha256`.
- Idempotency without decryption (review M3): the ciphertext is produced once and kept in private staging (file outside the public folders, removed after the copy is verified), its hash recorded and committed before the `PutObject`. A retry re-uploads the same staged bytes. A new encryption happens only when no staged ciphertext exists and the object is provably absent. Sidecars, already frozen before the first write, are staged the same way.

### Export (decided with the user, hardening my call)

- Export of encrypted copies needs the passphrase of every Own Company in scope; there is no export without it. Plaintext-only scopes work as today.
- Handover to the job (review M1): when the export starts, the server unlocks the identities right away, encrypts them to a fresh ephemeral age key and stores that blob on the export record; the ephemeral identity goes into Redis under a random token passed in the job kwargs and is read with GETDEL. A daily cleanup removes leftovers of failed or cancelled exports. The human passphrase never leaves the request. Docs require the cache Redis to run without persistence.
- Unlocks are rate-limited per user and company; failed unlocks are logged.
- The job passes identities and passphrases to `age` only through `pass_fds` pipes (`-i /dev/fd/N`, `AGE_PASSPHRASE_FD`), never through the environment or temp files, with core dumps disabled (review L2; covered by an integration test).
- Decryption writes to a temp file and uses it only on exit code 0 and a matching plaintext hash; partial plaintext never goes into the ZIP (review L1).
- Export detects encryption from the object itself (`age-inspect`); a mismatch with the copy's flag is a hard error per object.
- The package contents stay the same: decrypted bytes under `documents/`, plaintext SHA-256 in the manifest, the same viewer. The manifest also records per object whether it was encrypted and with which key fingerprint.
- The finished ZIP is plaintext and sits in Frappe's file storage for **Keep Exports For (Days)**; documented in the TOMs.
- Residual risk, accepted: an attacker controlling the server during an export can capture the unlocked identities.

### Recovery without Frappe (decided with the user, hardening my call)

- On every encrypting target docarchive writes a locked, unencrypted recovery kit under `_recovery/<version>/`: `FORMAT.md`, the age specification and CCTV test vectors, the age source tarball, release binaries for Linux, macOS and Windows with their sigsum proofs, and a Frappe-free recovery script (Python standard library plus `age`, with its Python version noted). Only an allowlist of files with SHA-256 values pinned in the app's code is uploaded (review H1). A new version goes into a new folder on every age upgrade.
- The kit in the bucket is a convenience only, since anyone with the archive key could plant a newer folder (review H3). The authoritative copy is the desk download, which the customer keeps offline next to the recovery key; the recovery sheet carries the kit's SHA-256. The docs, outside the bucket, state the minimum: a sigsum-verified official `age` and `age -d -i recovery.txt`.
- The recovery script decrypts into a folder and builds the offline viewer from the decrypted `.meta.json` files (reusing `export_viewer.html` and `export_viewer_core.js`). It uses the oldest locked version per key, reports keys with several versions and objects no `.meta.json` references, and sanitizes object keys into paths.
- Residual risk, documented: anyone with write access to the bucket and the public keys can create well-formed age objects. In the database path the recorded version IDs and ciphertext hashes catch this; in the Frappe-free path the oldest-locked-version rule and the reports above do.

### Managed offering (from review, documentation only)

- The operator stores ciphertext; the customer holds keys and passphrase and stays responsible for Lesbarmachung and data access (§ 147 Abs. 6 AO, the operator is a "Dritter"). The operator provides ciphertext, the kit and a self-service download of the whole bucket.
- "The operator cannot read" holds only when the operator stores the archive but does not run the customer's Frappe (which sees plaintext, working copies and, if generated there, the company key).
- Encryption does not remove processor status (Art. 28 GDPR): processor agreement with Hetzner as sub-processor stays. Contract end: COMPLIANCE objects cannot be deleted early; the contract covers storage until expiry, deletion at the earliest date and hand-back of the ciphertext.
- Crypto-shredding is not offered as erasure: keys are per company and the recovery key has offline copies. Deletion after retention stays version-based as today.

### Interaction with #50 (from review)

Today auditors read (Z1/Z2) through the working copy. Once #50 removes working copies, documents on encrypted targets can only be read with a passphrase. #50 must not remove working copies of documents whose only verified copies are encrypted until an "auditor session" exists (a key holder unlocks for a limited time, in memory, logged). To be noted in #50.

### Audit log entries stay plaintext (decided with the user)

Audit objects are written unencrypted on all targets, as today. The bucket copy is the independent witness: the nightly check compares it with the database copy, which only works while the server can read it, and auditors can verify it without a passphrase. Encrypting would also need per-company splitting (entries span several or no companies). Entries hold user IDs, document IDs, actions and reason codes, no subjects or addresses; the builder checks that no free-text reasons (e.g. legal hold reasons) end up in entries. Possible later extension: split the user ID off (pseudonym in the bucket, mapping backed up separately), leaving the neutral part plaintext.

## Left to the builder

- Field names and layout on Own Company, Archive Storage Target and the copy rows; the UI for key setup, proof, passphrase change, new keys, custody and drills.
- How and when the recovery kit and the `_keys` pins are written, and their retention.
- The `age` wrapper (streaming, file descriptors, error mapping), kept in one module.
- Tests: round trip with the real binary, mixed archives, decryption of an archived object with only `age -d -i recovery.txt`, refusal paths, retry after a crash at each step, fd handover to batchpass, `age-inspect` checks.
- Documentation: `docs/storage.md`, `docs/export.md`, "Keys and custody", the Verfahrensdokumentation template, a short crypto rationale (algorithms, scrypt work factor, deviations from BSI TR-02102 such as X25519 and ChaCha20-Poly1305 and why they are acceptable; no "BSI-compliant" claim), the Hetzner example and the Dockerfile snippet, the Art. 30 record entry.
