import * as core from "./documents_core";

frappe.provide("docarchive");
docarchive.documents_core = core;

const METHOD = "docarchive.documents";
const DOCUMENT = "Business Document";
const esc = (value) => frappe.utils.escape_html(value ?? "");
const panels = new Set();
let listening = false;

const STATE_STYLES = {
	open: ["orange", "Open"],
	complete: ["blue", "Complete"],
	archived: ["green", "Archived"],
	failed: ["red", "Failed"],
	deleted: ["gray", "Deleted"],
};
const storage = () => window.localStorage;

docarchive.file_url ??= (file) => file;

function state_badge(doc) {
	const state = core.document_state(doc);
	const [color, label] = STATE_STYLES[state];
	if (state !== "archived") return `<span class="indicator-pill ${color}">${esc(__(label))}</span>`;
	const until = doc.retain_until
		? __("Unchangeable until {0}", [docarchive.format_retain_until(doc.retain_until)])
		: "";
	return `<span class="indicator-pill green" title="${esc(until)}">${frappe.utils.icon(
		"lock",
		"xs",
	)} ${esc(__(label))}</span>`;
}

function select(name, label, options, value) {
	const items = [["", label], ...options];
	if (value && !items.some(([key]) => key === value)) items.push([value, value]);
	const html = items
		.map(
			([key, text]) =>
				`<option value="${esc(key)}" ${key === value ? "selected" : ""}>${esc(text)}</option>`,
		)
		.join("");
	return `<select class="form-control input-xs" data-filter="${name}" aria-label="${esc(label)}">${html}</select>`;
}

const sort_label = (filters) => (filters.oldest_first ? __("Oldest first") : __("Newest first"));
const name_of = (event) => $(event.currentTarget).closest("tr").attr("data-name");

class DocumentsPanel {
	constructor($wrapper, party) {
		this.$wrapper = $wrapper;
		this.party = party;
		this.key = core.filter_key(party.counterparty_type, party.counterparty);
		this.filters = core.load_filters(storage, this.key);
		this.selected = new Set();
	}

	load() {
		return frappe.xcall(`${METHOD}.counterparty_panel`, this.party).then((data) => {
			this.data = data;
			const eligible = new Set(data.documents.filter((doc) => doc.eligible).map((doc) => doc.name));
			this.selected = new Set([...this.selected].filter((name) => eligible.has(name)));
			this.render();
			return data.documents;
		});
	}

	render() {
		this.$wrapper.html(`<div class="docarchive-panel">
			${this.data.documents.length ? this.toolbar() : ""}
			<div class="docarchive-body"></div>
			<div class="docarchive-footer text-muted small"></div>
		</div>`);
		this.$panel = this.$wrapper.find(".docarchive-panel");
		this.bind_toolbar();
		this.bind_body();
		if (this.data.can_create) this.bind_drop();
		this.render_body();
	}

	toolbar() {
		const { document_types, years, retain_years } = core.filter_options(this.data.documents);
		const types = document_types.map((type) => [type, __(type)]);
		const states = core.STATES.map((state) => [state, __(STATE_STYLES[state][1])]);
		const upload = this.data.can_create
			? `<button class="btn btn-primary btn-xs" data-action="upload">${esc(__("Upload"))}</button>`
			: "";
		const extend =
			this.data.can_extend && this.data.documents.some((doc) => doc.archive_status === "Archived")
				? `<button class="btn btn-default btn-xs" data-action="extend-all">${esc(__("Extend all documents of this counterparty"))}</button>`
				: "";
		const exporter = this.data.can_export
			? `<button class="btn btn-default btn-xs" data-action="export">${esc(__("Export"))}</button>`
			: "";
		return `<div class="docarchive-toolbar">
			<input type="search" class="form-control input-xs docarchive-search" data-filter="search"
				placeholder="${esc(__("Search title or number"))}" value="${esc(this.filters.search)}">
			${select("document_type", __("All types"), types, this.filters.document_type)}
			${select("year", __("All years"), years.map((year) => [year, year]), this.filters.year)}
			${select("state", __("All statuses"), states, this.filters.state)}
			${select(
				"retain_year",
				__("Kept until: any year"),
				retain_years.map((year) => [year, __("Kept until {0}", [year])]),
				this.filters.retain_year,
			)}
			<select class="form-control input-xs" data-filter="sort_by" aria-label="${esc(__("Sort by"))}">
				${[
					["document_date", __("Document Date")],
					["retain_until", __("Retain Until (UTC)")],
				]
					.map(
						([key, text]) =>
							`<option value="${key}" ${key === (this.filters.sort_by || "document_date") ? "selected" : ""}>${esc(text)}</option>`,
					)
					.join("")}
			</select>
			<button class="btn btn-default btn-xs" data-action="sort">${esc(sort_label(this.filters))}</button>
			${
				this.data.documents.some((doc) => doc.archive_status === core.DELETED)
					? `<label class="docarchive-toggle"><input type="checkbox" data-filter-check="show_deleted"
						${this.filters.show_deleted ? "checked" : ""}> ${esc(__("Show deleted"))}</label>`
					: ""
			}
			<span class="docarchive-spacer"></span>
			<button class="btn btn-default btn-xs hidden" data-action="bulk-archive"></button>
			${extend}
			${exporter}
			${upload}
		</div>`;
	}

	bind_toolbar() {
		const $toolbar = this.$panel.find(".docarchive-toolbar");
		$toolbar.on("input change", "[data-filter]", (event) =>
			this.set_filter(event.currentTarget.dataset.filter, event.currentTarget.value),
		);
		$toolbar.on("change", "[data-filter-check]", (event) =>
			this.set_filter(event.currentTarget.dataset.filterCheck, event.currentTarget.checked),
		);
		$toolbar.on("click", "[data-action=sort]", (event) => {
			this.set_filter("oldest_first", !this.filters.oldest_first);
			$(event.currentTarget).text(sort_label(this.filters));
		});
		$toolbar.on("click", "[data-action=upload]", () => this.upload());
		$toolbar.on("click", "[data-action=export]", () => docarchive.export_archive(this.party));
		$toolbar.on("click", "[data-action=extend-all]", () =>
			docarchive.extend_many(this.party, () => this.load()),
		);
		$toolbar.on("click", "[data-action=bulk-archive]", () =>
			docarchive.archive_now([...this.selected], () => this.load()),
		);
	}

	bind_body() {
		const $body = this.$panel.find(".docarchive-body");
		$body.on("change", "[data-select]", (event) => {
			const name = event.currentTarget.dataset.select;
			if (event.currentTarget.checked) this.selected.add(name);
			else this.selected.delete(name);
			this.render_body();
		});
		$body.on("change", "[data-select-all]", (event) => {
			for (const doc of this.rows.filter((row) => row.eligible)) {
				if (event.currentTarget.checked) this.selected.add(doc.name);
				else this.selected.delete(doc.name);
			}
			this.render_body();
		});
		$body.on("click", "[data-action=copy]", (event) =>
			frappe.utils.copy_to_clipboard(
				frappe.urllib.get_full_url(frappe.utils.get_form_link(DOCUMENT, name_of(event))),
			),
		);
		$body.on("click", "[data-action=archive]", (event) =>
			docarchive.archive_now([name_of(event)], () => this.load()),
		);
		$body.on("click", "[data-action=thread]", (event) => docarchive.show_thread(name_of(event)));
		$body.on("click", ".docarchive-dropzone", () => this.upload());
		$body.on("keydown", ".docarchive-dropzone", (event) => {
			if (event.key === "Enter" || event.key === " ") this.upload();
		});
	}

	bind_drop() {
		this.$panel
			.on("dragover", (event) => {
				event.preventDefault();
				event.stopPropagation();
				this.$panel.addClass("docarchive-dragging");
			})
			.on("dragleave", (event) => {
				if (!this.$panel[0].contains(event.relatedTarget)) {
					this.$panel.removeClass("docarchive-dragging");
				}
			})
			.on("drop", (event) => {
				event.preventDefault();
				event.stopPropagation();
				this.$panel.removeClass("docarchive-dragging");
				const files = [...(event.originalEvent.dataTransfer?.files || [])];
				if (files.length) this.upload(files);
			});
	}

	set_filter(name, value) {
		this.filters[name] = value;
		core.save_filters(storage, this.key, this.filters);
		this.render_body();
	}

	upload(files) {
		docarchive.upload_documents({ ...this.party, files, on_saved: () => this.load() });
	}

	render_body() {
		const { documents } = this.data;
		this.rows = core.filter_documents(documents, this.filters);
		const $body = this.$panel.find(".docarchive-body");
		if (!documents.length) $body.html(this.empty_state());
		else if (!this.rows.length) {
			$body.html(`<p class="text-muted">${esc(__("No documents match the filters."))}</p>`);
		} else $body.html(this.table(this.rows));
		this.render_footer();
		this.$panel
			.find("[data-action=bulk-archive]")
			.toggleClass("hidden", !this.selected.size)
			.text(`${__("Archive now")} (${this.selected.size})`);
	}

	empty_state() {
		if (!this.data.can_create) return `<p class="text-muted">${esc(__("No documents yet"))}</p>`;
		return `<div class="docarchive-dropzone" role="button" tabindex="0">
			${frappe.utils.icon("upload", "md")}
			<span>${esc(__("No documents yet. Drop files here."))}</span>
		</div>`;
	}

	table(rows) {
		const selectable = this.data.archiving && this.data.can_write;
		const eligible = rows.filter((doc) => doc.eligible);
		const all = eligible.length && eligible.every((doc) => this.selected.has(doc.name));
		const select_all = selectable
			? `<th class="docarchive-check"><input type="checkbox" data-select-all ${all ? "checked" : ""}
				${eligible.length ? "" : "disabled"} aria-label="${esc(__("Select all"))}"></th>`
			: "";
		const headers = [
			"Title",
			"Business Document Type",
			"Document Number",
			"Document Date",
			"Retain Until (UTC)",
			"Status",
		];
		return `<table class="table table-condensed docarchive-table">
			<thead><tr>${select_all}${headers.map((h) => `<th>${esc(__(h))}</th>`).join("")}<th></th></tr></thead>
			<tbody>${rows.map((doc) => this.row(doc, selectable)).join("")}</tbody>
		</table>`;
	}

	row(doc, selectable) {
		const href = frappe.utils.get_form_link(DOCUMENT, doc.name);
		const missing = (doc.missing || []).map((field) => __("{0} missing", [__(field)])).join(", ");
		const checked = this.selected.has(doc.name) ? "checked" : "";
		const check = doc.eligible
			? `<input type="checkbox" data-select="${esc(doc.name)}" ${checked} aria-label="${esc(__("Select"))}">`
			: "";
		const file = doc.file
			? `<a class="btn btn-xs btn-default" href="${esc(docarchive.file_url(doc.file))}" target="_blank"
				rel="noopener" title="${esc(__("Open file"))}">${frappe.utils.icon("file-text", "xs")}</a>`
			: "";
		const thread = doc.thread_key
			? `<button class="btn btn-xs btn-default" data-action="thread" title="${esc(__("Show thread"))}">
				${frappe.utils.icon("mail", "xs")}</button>`
			: "";
		const archive = doc.eligible
			? `<button class="btn btn-xs btn-default" data-action="archive">${esc(__("Archive now"))}</button>`
			: "";
		return `<tr data-name="${esc(doc.name)}">
			${selectable ? `<td class="docarchive-check">${check}</td>` : ""}
			<td>
				<a href="${esc(href)}">${esc(doc.title || doc.name)}</a>
				${missing ? `<a href="${esc(href)}" class="docarchive-missing">${esc(missing)}</a>` : ""}
			</td>
			<td>${esc(__(doc.document_type))}</td>
			<td class="docarchive-nowrap">${esc(doc.document_number)}</td>
			<td class="docarchive-nowrap">${esc(frappe.datetime.str_to_user(doc.document_date))}</td>
			<td class="docarchive-nowrap">${esc(doc.retain_until ? frappe.datetime.str_to_user(doc.retain_until.slice(0, 10)) : "")}</td>
			<td class="docarchive-nowrap">${state_badge(doc)}</td>
			<td class="docarchive-actions">
				${file}
				<button class="btn btn-xs btn-default" data-action="copy" title="${esc(__("Copy link"))}">
					${frappe.utils.icon("link", "xs")}</button>
				${thread}
				${archive}
			</td>
		</tr>`;
	}

	render_footer() {
		const { documents, truncated } = this.data;
		const $footer = this.$panel.find(".docarchive-footer").empty();
		if (documents.length) {
			const count = __("{0} of {1} documents", [this.rows.length, documents.length]);
			$footer.append(`<span>${esc(count)}</span>`);
		}
		if (truncated) {
			$footer.append(`<span>${esc(__("Only the newest {0} are shown.", [documents.length]))}</span>`);
		}
		$(`<a href="#">${esc(__("Show all documents"))}</a>`)
			.appendTo($footer)
			.on("click", (event) => {
				event.preventDefault();
				frappe.set_route("List", DOCUMENT, this.party);
			});
	}
}

function file_rows(files, defaults) {
	return files.map(({ file_url, file_name }) => {
		const parsed = core.date_from_file_name(file_name || file_url);
		return {
			file_url,
			file_name,
			title: parsed.title,
			document_type: defaults.document_type,
			document_date: parsed.date || defaults.document_date,
		};
	});
}

const UPLOAD_COLUMNS = [
	{ fieldname: "file_url", fieldtype: "Data", hidden: 1 },
	{ fieldname: "file_name", fieldtype: "Data", label: "File", read_only: 1, columns: 3 },
	{ fieldname: "title", fieldtype: "Data", label: "Title", columns: 3 },
	{
		fieldname: "document_type",
		fieldtype: "Link",
		options: "Business Document Type",
		label: "Business Document Type",
		reqd: 1,
		columns: 2,
	},
	{ fieldname: "document_date", fieldtype: "Date", label: "Document Date", columns: 2 },
];

const CORRESPONDENCE_FIELDS = ["correspondence_channel", "correspondence_direction", "correspondence_at"];

function correspondence_fields() {
	return [
		{ fieldname: "correspondence_section", fieldtype: "Section Break", label: __("Correspondence") },
		{
			fieldname: "correspondence_channel",
			fieldtype: "Select",
			label: __("Channel"),
			options: ["", "Letter", "Fax", "Other"].map((value) => ({ value, label: value && __(value) })),
		},
		{ fieldname: "column_break_correspondence", fieldtype: "Column Break" },
		{
			fieldname: "correspondence_direction",
			fieldtype: "Select",
			label: __("Direction"),
			options: ["", "Incoming", "Outgoing"].map((value) => ({ value, label: value && __(value) })),
		},
		{ fieldname: "column_break_correspondence_at", fieldtype: "Column Break" },
		{ fieldname: "correspondence_at", fieldtype: "Datetime", label: __("Received or sent at") },
	].map((df) => ({ ...df, hidden: 1 }));
}

function ask_details(party, files, on_saved, extra = {}) {
	let saved = false;
	const file_urls = files.map((file) => file.file_url);
	frappe.xcall(`${METHOD}.upload_defaults`).then((server) => {
		const defaults = { ...server, ...extra };
		const letters = new Set(defaults.correspondence_types || []);
		let dialog;
		const toggle_correspondence = () => {
			if (!dialog) return;
			const rows = dialog.get_value("documents") || [];
			const hidden = !rows.some((row) => letters.has(row.document_type));
			for (const field of ["correspondence_section", ...CORRESPONDENCE_FIELDS]) {
				dialog.set_df_property(field, "hidden", hidden);
			}
		};
		dialog = new frappe.ui.Dialog({
			title: __("Upload documents"),
			size: "extra-large",
			fields: [
				{
					fieldname: "documents",
					fieldtype: "Table",
					label: __("Documents"),
					cannot_add_rows: true,
					cannot_delete_rows: true,
					in_place_edit: true,
					data: file_rows(files, defaults),
					fields: UPLOAD_COLUMNS.map((df) => ({
						...df,
						label: df.label && __(df.label),
						in_list_view: df.hidden ? 0 : 1,
						onchange: df.fieldname === "document_type" ? () => toggle_correspondence() : undefined,
					})),
				},
				{
					fieldname: "combine",
					fieldtype: "Check",
					label: __("One document with attachments"),
					description: __("The first file is the main file, the others become its attachments."),
					hidden: file_urls.length < 2,
				},
				...correspondence_fields(),
			],
			primary_action_label: __("Save"),
			primary_action(values) {
				const documents = (values.documents || []).map((row) => ({
					file_url: row.file_url,
					title: row.title,
					document_type: row.document_type,
					document_date: row.document_date,
				}));
				const source = defaults.source_name
					? { source_doctype: defaults.source_doctype, source_name: defaults.source_name }
					: {};
				const correspondence = Object.fromEntries(
					CORRESPONDENCE_FIELDS.filter((field) => values[field]).map((field) => [field, values[field]]),
				);
				frappe
					.call({
						method: `${METHOD}.create_documents`,
						args: { ...party, documents, combine: values.combine, ...source, ...correspondence },
						freeze: true,
					})
					.then(() => {
						saved = true;
						dialog.hide();
						on_saved && on_saved();
					});
			},
		});
		dialog.onhide = () => saved || frappe.xcall(`${METHOD}.discard_uploads`, { file_urls });
		dialog.show();
		toggle_correspondence();
	});
}

docarchive.upload_documents = function ({
	counterparty_type,
	counterparty,
	files,
	on_saved,
	defaults,
}) {
	const uploaded = [];
	const uploader = new frappe.ui.FileUploader({
		allow_multiple: true,
		disable_file_browser: true,
		make_attachments_public: false,
		allow_toggle_private: false,
		files,
		on_success: (file) => uploaded.push({ file_url: file.file_url, file_name: file.file_name }),
	});
	const party = { counterparty_type, counterparty };
	uploader.dialog.onhide = () => {
		const emls = uploaded.filter((file) => docarchive.mail_core.is_eml(file.file_name || file.file_url));
		const others = uploaded.filter((file) => !emls.includes(file));
		const details = () => others.length && ask_details(party, others, on_saved, defaults);
		if (!emls.length) return details();
		docarchive.archive_emls(emls, {
			...party,
			source_doctype: defaults?.source_name ? defaults.source_doctype : undefined,
			source_name: defaults?.source_name,
			on_saved: (names) => {
				if (names.length && on_saved) on_saved();
				details();
			},
		});
	};
};

docarchive.render_documents = function ($wrapper, { counterparty_type, counterparty }) {
	$wrapper.empty();
	if (!frappe.model.can_read(DOCUMENT)) return Promise.resolve([]);
	const panel = new DocumentsPanel($wrapper, { counterparty_type, counterparty });
	$wrapper.data("docarchive-panel", panel);
	if (!listening) frappe.realtime.on("docarchive_archive_now", refresh_panels);
	listening = true;
	panels.add(panel);
	return panel.load();
};

docarchive.documents_panel = function (frm, fieldname, section) {
	const can_read = frappe.model.can_read(DOCUMENT) && !frm.is_new();
	if (section) frm.toggle_display(section, can_read);
	if (!can_read) return;
	return docarchive.render_documents(frm.get_field(fieldname).$wrapper, {
		counterparty_type: frm.doctype,
		counterparty: frm.doc.name,
	});
};

function refresh_panels(data) {
	for (const panel of panels) {
		if (!panel.$wrapper[0].isConnected || panel.$wrapper.data("docarchive-panel") !== panel) {
			panels.delete(panel);
		} else if (panel.data?.documents.some((doc) => doc.name === data.name)) {
			panel.load();
		}
	}
}

