import { Queue, initial_date, key_action, preview_kind } from "./triage_core";

frappe.provide("docarchive");

const METHOD = "docarchive.triage";
const esc = (value) => frappe.utils.escape_html(value ?? "");
const FIELDS = [
	"title",
	"document_type",
	"document_date",
	"document_number",
	"counterparty_type",
	"counterparty",
	"contract_end_date",
];

function preview_html(item) {
	const kind = preview_kind(item.file);
	const name = esc(item.file_name || item.file || "");
	if (!item.file) return `<div class="docarchive-triage-empty text-muted">${esc(__("No file"))}</div>`;
	if (kind === "pdf") return `<iframe class="docarchive-triage-frame" src="${esc(item.file)}" title="${name}"></iframe>`;
	if (kind === "image") return `<img class="docarchive-triage-image" src="${esc(item.file)}" alt="${name}">`;
	return `<div class="docarchive-triage-empty">
		<p>${name}</p>
		<a class="btn btn-default btn-sm" href="${esc(item.file)}" download>${esc(__("Download"))}</a>
	</div>`;
}

function prefetch(item) {
	if (!item?.file || preview_kind(item.file) === "other") return;
	if (document.querySelector(`link[rel=prefetch][href="${CSS.escape(item.file)}"]`)) return;
	$("<link>", { rel: "prefetch", href: item.file }).appendTo(document.head);
}

class Triage {
	constructor(names, { start = 0, on_close } = {}) {
		this.queue = new Queue(names, start);
		this.items = new Map();
		this.on_close = on_close;
		this.saved = 0;
	}

	async open() {
		const doctypes = await frappe.xcall("docarchive.counterparty.doctypes");
		this.dialog = this.make_dialog(doctypes);
		this.dialog.show();
		$(document).on("keydown.docarchive-triage", (event) => this.on_key(event));
		this.dialog.onhide = () => {
			$(document).off("keydown.docarchive-triage");
			this.on_close?.({ saved: this.saved });
		};
		await this.show_current();
	}

	make_dialog(doctypes) {
		const dialog = new frappe.ui.Dialog({
			title: __("Assign documents"),
			size: "extra-large",
			fields: [
				{ fieldname: "preview", fieldtype: "HTML" },
				{ fieldtype: "Column Break" },
				{ fieldname: "file_info", fieldtype: "HTML" },
				{ fieldname: "title", fieldtype: "Data", label: __("Title") },
				{
					fieldname: "document_type",
					fieldtype: "Link",
					options: "Business Document Type",
					label: __("Business Document Type"),
					reqd: 1,
					onchange: () => this.update_contract_end(),
				},
				{ fieldname: "document_date", fieldtype: "Date", label: __("Document Date"), reqd: 1 },
				{ fieldname: "document_number", fieldtype: "Data", label: __("Document Number") },
				{
					fieldname: "counterparty_type",
					fieldtype: "Link",
					options: "DocType",
					label: __("Counterparty Type"),
					get_query: () => ({
						filters: doctypes.length ? { name: ["in", doctypes] } : { istable: 0, issingle: 0 },
					}),
					onchange: () => this.counterparty_type_changed(),
				},
				{
					fieldname: "counterparty",
					fieldtype: "Dynamic Link",
					options: "counterparty_type",
					label: __("Counterparty"),
					get_options: () => this.dialog.get_value("counterparty_type"),
				},
				{ fieldname: "contract_end_date", fieldtype: "Date", label: __("Contract End Date") },
				{ fieldname: "missing", fieldtype: "HTML" },
				{ fieldname: "error", fieldtype: "HTML" },
			],
			primary_action_label: __("Save and next"),
			primary_action: () => this.save_and_next(),
			secondary_action_label: __("Skip"),
			secondary_action: () => this.move(1),
		});
		dialog.$wrapper.addClass("docarchive-triage");
		dialog.add_custom_action(__("Back"), () => this.move(-1), "docarchive-triage-back");
		this.$back = dialog.custom_actions.find(".docarchive-triage-back");
		return dialog;
	}

	on_key(event) {
		const action = key_action(event);
		if (!action || action === "close") return;
		event.preventDefault();
		event.stopPropagation();
		if (action === "save_next") this.save_and_next();
		if (action === "previous") this.move(-1);
		if (action === "next") this.move(1);
	}

	load(name) {
		if (!name) return Promise.resolve(null);
		if (!this.items.has(name)) {
			this.items.set(
				name,
				frappe.xcall(`${METHOD}.triage_item`, { name }).catch((error) => {
					this.items.delete(name);
					throw error;
				}),
			);
		}
		return this.items.get(name);
	}

	async show_current() {
		const name = this.queue.current;
		const item = await this.load(name);
		if (name !== this.queue.current) return;
		this.item = item;
		this.render(item);
		this.load(this.queue.upcoming).then(prefetch, () => null);
	}

	async render(item) {
		const { dialog, queue } = this;
		const { current, total } = queue.position;
		dialog.set_title(`${__("Assign documents")} · ${__("{0} of {1}", [current, total])}`);
		dialog.fields_dict.preview.$wrapper.html(preview_html(item));
		const $frame = dialog.fields_dict.preview.$wrapper.find("iframe");
		$frame.on("load", () =>
			setTimeout(() => document.activeElement === $frame[0] && this.focus_form(), 300),
		);
		const kept = item.retain_until
			? `<p class="text-muted">${esc(__("Retain Until (UTC)"))}: ${esc(docarchive.format_retain_until(String(item.retain_until)))}</p>`
			: "";
		dialog.fields_dict.file_info.$wrapper.html(
			`<p class="docarchive-triage-file">${esc(item.file_name || __("No file"))}</p>${kept}`,
		);
		this.show_error();
		this.party_type = item.counterparty_type;
		this.type = item.document_type;
		await dialog.set_values({ ...pick(item), document_date: initial_date(item) });
		const editable = item.can_write && !item.locked;
		for (const fieldname of FIELDS) dialog.set_df_property(fieldname, "read_only", editable ? 0 : 1);
		dialog.set_df_property("contract_end_date", "hidden", item.needs_contract_end ? 0 : 1);
		dialog.get_primary_btn().prop("disabled", !editable);
		this.$back.prop("disabled", queue.is_first);
		this.render_missing(item);
		this.focus_form();
	}

	focus_form() {
		const input = this.dialog.fields_dict.title.$input;
		if (this.item?.can_write && !this.item.locked) input.trigger("focus");
		else this.dialog.get_primary_btn().trigger("focus");
	}

	render_missing(item) {
		const notes = [];
		if (item.locked) notes.push(__("This document is being archived and cannot be changed."));
		else if (!item.can_write) notes.push(__("You cannot change this document."));
		const missing = item.missing.length
			? `<p class="text-warning">${esc(__("Still missing: {0}", [item.missing.map((f) => __(f)).join(", ")]))}</p>`
			: `<p class="text-success">${esc(__("Complete"))}</p>`;
		this.dialog.fields_dict.missing.$wrapper.html(
			missing + notes.map((note) => `<p class="text-muted">${esc(note)}</p>`).join(""),
		);
	}

	show_error(message) {
		this.dialog.fields_dict.error.$wrapper.html(
			message ? `<div class="alert alert-danger docarchive-triage-error">${esc(message)}</div>` : "",
		);
	}

	counterparty_type_changed() {
		const party_type = this.dialog.get_value("counterparty_type");
		if (party_type === this.party_type) return;
		this.party_type = party_type;
		this.dialog.set_value("counterparty", null);
	}

	update_contract_end() {
		const document_type = this.dialog.get_value("document_type");
		if (!document_type || document_type === this.type) return;
		this.type = document_type;
		frappe.xcall(`${METHOD}.document_type_rules`, { document_type }).then(({ needs_contract_end }) =>
			this.dialog.set_df_property("contract_end_date", "hidden", needs_contract_end ? 0 : 1),
		);
	}

	async save_and_next() {
		if (this.saving || !this.item?.can_write || this.item.locked) return;
		this.saving = true;
		const name = this.queue.current;
		try {
			const result = await frappe.xcall(`${METHOD}.save_item`, {
				name,
				values: pick(this.dialog.get_values(true)),
			});
			if (result.error) return this.show_error(result.error);
			this.saved += 1;
			this.items.set(name, Promise.resolve(result.item));
			this.move(1);
		} finally {
			this.saving = false;
		}
	}

	move(delta) {
		const moved = delta > 0 ? this.queue.next() : this.queue.previous();
		if (moved) return this.show_current();
		if (delta > 0) {
			this.dialog.hide();
			frappe.show_alert({ message: __("All documents processed."), indicator: "green" });
		}
	}
}

function pick(values) {
	return Object.fromEntries(FIELDS.map((field) => [field, values?.[field] ?? null]));
}

docarchive.triage = function (names, options = {}) {
	if (!names?.length) {
		frappe.show_alert({ message: __("Nothing to assign."), indicator: "green" });
		return Promise.resolve();
	}
	return new Triage(names, options).open();
};

docarchive.triage_all = function (options = {}) {
	return frappe
		.xcall(`${METHOD}.open_documents`)
		.then((names) => docarchive.triage(names, options));
};
