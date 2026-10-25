import json
import sys

import click
from frappe.commands import get_site, pass_context


def connect(context):
	import frappe

	frappe.init(get_site(context))
	frappe.connect()
	return frappe


@click.command("docarchive-run")
@click.option("--dry-run", is_flag=True, help="Only list what would be archived")
@pass_context
def run_archive(context, dry_run=False):
	"""Archive eligible documents to all enabled storage targets."""
	frappe = connect(context)
	from docarchive import run

	try:
		result = run.nightly(dry_run=dry_run)
	finally:
		frappe.destroy()
	click.echo(json.dumps(result, indent=1, default=str))
	if result.get("failed") or result.get("extension_failed"):
		sys.exit(1)


@click.command("docarchive-smoke")
@click.option("--target", required=True, help="Name of the storage target")
@pass_context
def smoke(context, target):
	"""Upload a tiny object with a 1-day GOVERNANCE lock and check that it cannot be deleted."""
	frappe = connect(context)
	from docarchive import run

	try:
		result = run.smoke(target)
	finally:
		frappe.destroy()
	click.echo(json.dumps(result, indent=1, default=str))
	if not result["ok"]:
		sys.exit(1)


@click.command("docarchive-sync-mode")
@click.option("--target", required=True, help="Name of the storage target")
@pass_context
def sync_mode(context, target):
	"""Record the lock mode that the target reports for each archived document."""
	frappe = connect(context)
	from docarchive import run

	try:
		result = run.sync_mode(target)
	finally:
		frappe.destroy()
	click.echo(json.dumps(result, indent=1, default=str))
	if result["mismatched"] or result["errors"]:
		sys.exit(1)


commands = [run_archive, smoke, sync_mode]
