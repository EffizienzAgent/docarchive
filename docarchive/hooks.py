app_name = "docarchive"
app_title = "Document Archive"
app_publisher = "EffizienzAgent"
app_description = "A WORM document archive app for the Frappe Framework"
app_email = "oss@effizienzagent.de"
app_license = "mit"

required_apps = ["s3storage"]

app_include_js = "docarchive.bundle.js"

after_install = "docarchive.setup.apply_defaults"
after_migrate = "docarchive.setup.apply_defaults"

doc_events = {
	"File": {"before_validate": "docarchive.guards.protect_file_change"},
	"Communication": {"on_trash": "docarchive.mail.originals.on_communication_trash"},
	"*": {"before_rename": "docarchive.guards.protect_party"},
}
extend_doctype_class = {
	"Email Account": ["docarchive.mail.extensions.EmailAccountArchive"],
	"Email Queue": ["docarchive.mail.extensions.EmailQueueArchive"],
}
scheduler_events = {"cron": {"30 2 * * *": ["docarchive.run.schedule"]}}
has_permission = {"File": "docarchive.guards.file_permission"}
s3storage_before_file_delete = ["docarchive.guards.protect_file"]
s3storage_before_object_delete = ["docarchive.guards.protect_object"]
s3storage_before_object_write = ["docarchive.guards.protect_object"]

# Extension hooks for host apps:
# docarchive_assign: callables that fill own_company / counterparty_type / counterparty on a document.
# docarchive_counterparty_doctypes: doctypes allowed as counterparty (empty: any).
# docarchive_read_file: callables returning the raw bytes of a file URL, or None.
