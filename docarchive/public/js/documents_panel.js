import * as core from "./documents_core";
import { local_file_url } from "./triage_core";

frappe.provide("docarchive");
docarchive.documents_core = core;

const METHOD = "docarchive.documents";
const DOCUMENT = "Business Document";
const WIDTH_KEY = "docarchive.preview_width";
const MIN_PREVIEW = 280;
const MIN_LIST = 320;
const esc = (value) => frappe.utils.escape_html(value ?? "");
const panels = new Set();
let listening = false;
let panel_ids = 0;

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

function selection_hint(doc) {
	if (doc.archive_status === core.DELETED) return __("Deleted documents cannot be changed.");
	if (doc.archive_status === "Archived" || !doc.archive_reason) {
		return __("No bulk action is available for this document.");
	}
	return doc.archive_reason;
}

const file_name = (url) => decodeURIComponent((url || "").split(/[?#]/)[0].split("/").pop() || "");

function safe_file_name(url) {
	try {
		return file_name(url);
	} catch {
		return url;
	}
}

function preview_files(doc) {
	const main = doc.file ? [{ file: doc.file, label: __("Main file") }] : [];
	const attachments = (doc.attachments || []).map((item) => ({
		file: item.file,
		label: item.title || safe_file_name(item.file),
	}));
	return [...main, ...attachments];
}

function read_width() {
	try {
		return Number(storage().getItem(WIDTH_KEY)) || null;
	} catch {
		return null;
	}
}

function save_width(width) {
	try {
		storage().setItem(WIDTH_KEY, String(Math.round(width)));
	} catch {
		// storage can be unavailable or full
	}
}

const is_typing = (target) =>
	target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName);

const sort_label = (filters) => (filters.oldest_first ? __("Oldest first") : __("Newest first"));
const name_of = (event) => $(event.currentTarget).closest("tr").attr("data-name");

class DocumentsPanel {
	constructor($wrapper, party) {
		this.$wrapper = $wrapper;
		this.party = party;
		this.id = ++panel_ids;
		this.key = core.filter_key(party.counterparty_type, party.counterparty);
		this.filters = core.load_filters(storage, this.key);
		this.selected = new Set();
		this.previewing = null;
		this.preview_file = 0;
	}

	load() {
		return frappe.xcall(`${METHOD}.counterparty_panel`, this.party).then(async (data) => {
			this.data = data;
			this.access = {
				extend: data.can_extend,
				export: data.can_export,
				hold: await this.hold_allowed(data),
			};
			this.render();
			return data.documents;
		});
	}

	hold_allowed(data) {
		const archived = data.documents.some((doc) => doc.archive_status === "Archived");
		if (!archived || !docarchive.hold_access) return false;
		return docarchive
			.hold_access()
			.then((access) => Boolean(access.set))
			.catch(() => false);
	}

	render() {
		this.$wrapper.html(`<div class="docarchive-panel">
			${this.data.documents.length ? this.toolbar() : ""}
			<div class="docarchive-bulkbar hidden" role="toolbar" aria-label="${esc(__("Selection"))}"></div>
			<div class="docarchive-split">
				<div class="docarchive-main">
					<div class="docarchive-body"></div>
					<div class="docarchive-footer text-muted small"></div>
				</div>
				<div class="docarchive-resizer hidden" role="separator" aria-orientation="vertical"
					tabindex="0" title="${esc(__("Drag to resize"))}"></div>
				<aside class="docarchive-preview hidden" aria-label="${esc(__("Preview"))}"></aside>
			</div>
		</div>`);
		this.$panel = this.$wrapper.find(".docarchive-panel");
		this.bind_toolbar();
		this.bind_bulkbar();
		this.bind_body();
		this.bind_preview();
		if (this.data.can_create) this.bind_drop();
		this.render_body();
		const doc = this.doc(this.previewing);
		if (doc) this.open_preview(doc.name, { keep_file: true });
		else this.close_preview();
	}

	doc(name) {
		return name ? this.data.documents.find((doc) => doc.name === name) : null;
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
	}

	bind_bulkbar() {
		const $bar = this.$panel.find(".docarchive-bulkbar");
		const reload = () => this.load();
		const run = (action) => {
			const names = core.bulk_targets(this.rows, this.selected, this.access)[action];
			if (!names.length) return;
			if (action === "archive") docarchive.archive_now(names, reload);
			if (action === "extend") docarchive.extend_many({ names }, reload);
			if (action === "hold") docarchive.set_legal_hold(names, reload);
			if (action === "export") {
				const docs = this.rows.filter((doc) => names.includes(doc.name));
				docarchive.export_archive(core.export_defaults(this.party, docs));
			}
		};
		$bar.on("click", "[data-bulk]", (event) => run(event.currentTarget.dataset.bulk));
		$bar.on("click", "[data-action=clear-selection]", () => {
			this.selected = new Set();
			this.update_selection();
		});
	}

	bind_body() {
		const $body = this.$panel.find(".docarchive-body");
		$body.on("change", "[data-select]", (event) => {
			const name = event.currentTarget.dataset.select;
			if (event.currentTarget.checked) this.selected.add(name);
			else this.selected.delete(name);
			this.update_selection();
		});
		$body.on("change", "[data-select-all]", (event) => {
			this.selected = core.toggle_all(
				this.rows,
				this.selected,
				this.access,
				event.currentTarget.checked,
			);
			this.update_selection();
		});
		$body.on("click", "[data-action=copy]", (event) =>
			frappe.utils.copy_to_clipboard(
				frappe.urllib.get_full_url(frappe.utils.get_form_link(DOCUMENT, name_of(event))),
			),
		);
		$body.on("click", "[data-action=archive]", (event) =>
			docarchive.archive_now([name_of(event)], () => this.load()),
		);
		$body.on("click", "[data-action=preview]", (event) => this.open_preview(name_of(event)));
		$body.on("click", "tbody tr[data-name]", (event) => {
			if ($(event.target).closest("a, button, input, label, .docarchive-check").length) return;
			this.open_preview(event.currentTarget.dataset.name);
		});
		$body.on("click", ".docarchive-dropzone", () => this.upload());
		$body.on("keydown", ".docarchive-dropzone", (event) => {
			if (event.key === "Enter" || event.key === " ") this.upload();
		});
	}

	bind_preview() {
		const $preview = this.$panel.find(".docarchive-preview");
		$preview.on("click", "[data-action=close-preview]", () => this.close_preview());
		$preview.on("click", "[data-file-index]", (event) => {
			this.preview_file = Number(event.currentTarget.dataset.fileIndex);
			this.render_preview();
		});
		const $resizer = this.$panel.find(".docarchive-resizer");
		$resizer.on("pointerdown", (event) => this.start_resize(event));
		$resizer.on("dblclick", () => this.set_width(null));
		$resizer.on("keydown", (event) => {
			const delta = { ArrowLeft: 32, ArrowRight: -32 }[event.key];
			if (!delta) return;
			event.preventDefault();
			this.set_width(this.$panel.find(".docarchive-preview").outerWidth() + delta);
		});
	}

	start_resize(event) {
		event.preventDefault();
		const $split = this.$panel.find(".docarchive-split").addClass("docarchive-resizing");
		const right = $split[0].getBoundingClientRect().right;
		const move = (e) => this.set_width(right - e.clientX, false);
		const stop = () => {
			$(document).off(`.docarchive-resize-${this.id}`);
			$split.removeClass("docarchive-resizing");
			save_width(this.$panel.find(".docarchive-preview").outerWidth());
		};
		$(document)
			.on(`pointermove.docarchive-resize-${this.id}`, (e) => move(e.originalEvent || e))
			.on(`pointerup.docarchive-resize-${this.id} pointercancel.docarchive-resize-${this.id}`, stop);
	}

	set_width(width, persist = true) {
		const $split = this.$panel.find(".docarchive-split");
		if (!width) {
			$split[0].style.removeProperty("--docarchive-preview-width");
			if (persist) save_width("");
			return;
		}
		const max = Math.max($split.width() - MIN_LIST, MIN_PREVIEW);
		const clamped = Math.min(Math.max(width, MIN_PREVIEW), max);
		$split[0].style.setProperty("--docarchive-preview-width", `${clamped}px`);
		if (persist) save_width(clamped);
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
		this.selected = core.prune_selection(this.rows, this.selected, this.access);
		const $body = this.$panel.find(".docarchive-body");
		if (!documents.length) $body.html(this.empty_state());
		else if (!this.rows.length) {
			$body.html(`<p class="text-muted">${esc(__("No documents match the filters."))}</p>`);
		} else $body.html(this.table(this.rows));
		this.render_footer();
		this.update_selection();
	}

	update_selection() {
		const state = core.selection_state(this.rows, this.selected, this.access);
		const $body = this.$panel.find(".docarchive-body");
		$body.find("[data-select]").each((_, input) => {
			input.checked = this.selected.has(input.dataset.select);
			$(input).closest("tr").toggleClass("docarchive-selected", input.checked);
		});
		$body
			.find("[data-select-all]")
			.prop({ checked: state.all, indeterminate: state.indeterminate });
		this.render_bulkbar(state.count);
	}

	render_bulkbar(count) {
		const $bar = this.$panel.find(".docarchive-bulkbar").toggleClass("hidden", !count);
		if (!count) return $bar.empty();
		const targets = core.bulk_targets(this.rows, this.selected, this.access);
		const labels = {
			archive: __("Archive now"),
			extend: __("Extend term"),
			hold: __("Legal Hold"),
			export: __("Export"),
		};
		const buttons = core.BULK_ACTIONS.filter((action) => targets[action].length).map((action) => {
			const n = targets[action].length;
			const label = n < count ? `${labels[action]} (${n})` : labels[action];
			return `<button class="btn btn-default btn-xs" data-bulk="${action}">${esc(label)}</button>`;
		});
		$bar.html(`<span class="docarchive-bulk-count">${esc(__("{0} selected", [count]))}</span>
			${buttons.join("")}
			<button class="btn btn-link btn-xs" data-action="clear-selection">${esc(__("Clear selection"))}</button>`);
	}

	empty_state() {
		if (!this.data.can_create) return `<p class="text-muted">${esc(__("No documents yet"))}</p>`;
		return `<div class="docarchive-dropzone" role="button" tabindex="0">
			${frappe.utils.icon("upload", "md")}
			<span>${esc(__("No documents yet. Drop files here."))}</span>
		</div>`;
	}

	table(rows) {
		const checks = this.data.documents.some((doc) => core.is_selectable(doc, this.access));
		const any = rows.some((doc) => core.is_selectable(doc, this.access));
		const select_all = any
			? `<input type="checkbox" class="list-row-checkbox" data-select-all aria-label="${esc(__("Select all"))}">`
			: "";
		const headers = [
			["Title", ""],
			["Business Document Type", ""],
			["Document Number", "docarchive-optional"],
			["Document Date", ""],
			["Retain Until (UTC)", "docarchive-optional"],
			["Status", ""],
		];
		return `<table class="table table-condensed docarchive-table">
			<thead><tr>
				${checks ? `<th class="docarchive-check">${select_all}</th>` : ""}
				${headers.map(([h, cls]) => `<th class="${cls}">${esc(__(h))}</th>`).join("")}
				<th class="docarchive-optional"></th>
			</tr></thead>
			<tbody>${rows.map((doc) => this.row(doc, checks)).join("")}</tbody>
		</table>`;
	}

	row(doc, checks) {
		const href = frappe.utils.get_form_link(DOCUMENT, doc.name);
		const missing = (doc.missing || []).map((field) => __("{0} missing", [__(field)])).join(", ");
		const check = core.is_selectable(doc, this.access)
			? `<input type="checkbox" class="list-row-checkbox" data-select="${esc(doc.name)}"
				aria-label="${esc(__("Select {0}", [doc.title || doc.name]))}">`
			: `<span class="docarchive-check-disabled" title="${esc(selection_hint(doc))}">
				<input type="checkbox" class="list-row-checkbox" disabled tabindex="-1"
					aria-label="${esc(selection_hint(doc))}"></span>`;
		const file = doc.file
			? `<button class="btn btn-xs btn-default" data-action="preview" title="${esc(__("Preview"))}">
					${frappe.utils.icon("eye", "xs")}</button>
				<a class="btn btn-xs btn-default" href="${esc(docarchive.file_url(doc.file))}" target="_blank"
					rel="noopener" title="${esc(__("Open in new tab"))}">${frappe.utils.icon("external-link", "xs")}</a>`
			: "";
		const archive = doc.eligible
			? `<button class="btn btn-xs btn-default" data-action="archive">${esc(__("Archive now"))}</button>`
			: "";
		const active = doc.name === this.previewing ? "docarchive-active" : "";
		return `<tr data-name="${esc(doc.name)}" class="${active}">
			${checks ? `<td class="docarchive-check">${check}</td>` : ""}
			<td>
				<a href="${esc(href)}">${esc(doc.title || doc.name)}</a>
				${missing ? `<a href="${esc(href)}" class="docarchive-missing">${esc(missing)}</a>` : ""}
			</td>
			<td>${esc(__(doc.document_type))}</td>
			<td class="docarchive-nowrap docarchive-optional">${esc(doc.document_number)}</td>
			<td class="docarchive-nowrap">${esc(frappe.datetime.str_to_user(doc.document_date))}</td>
			<td class="docarchive-nowrap docarchive-optional">${esc(doc.retain_until ? frappe.datetime.str_to_user(doc.retain_until.slice(0, 10)) : "")}</td>
			<td class="docarchive-nowrap">${state_badge(doc)}</td>
			<td class="docarchive-actions docarchive-optional">
				${file}
				<button class="btn btn-xs btn-default" data-action="copy" title="${esc(__("Copy link"))}">
					${frappe.utils.icon("link", "xs")}</button>
				${archive}
			</td>
		</tr>`;
	}

	open_preview(name, { keep_file = false } = {}) {
		if (!this.doc(name)) return;
		if (!keep_file || name !== this.previewing) this.preview_file = 0;
		this.previewing = name;
		this.$panel.addClass("docarchive-previewing");
		this.$panel.find(".docarchive-preview, .docarchive-resizer").removeClass("hidden");
		const width = read_width();
		if (width) this.set_width(width, false);
		const $rows = this.$panel.find("tbody tr[data-name]").removeClass("docarchive-active");
		const $row = $rows.filter((_, row) => row.dataset.name === name).addClass("docarchive-active");
		$row[0]?.scrollIntoView?.({ block: "nearest" });
		this.render_preview();
		this.listen_keys();
	}

	close_preview() {
		this.previewing = null;
		this.preview_request = null;
		this.$panel.removeClass("docarchive-previewing");
		this.$panel.find(".docarchive-preview").addClass("hidden").empty();
		this.$panel.find(".docarchive-resizer").addClass("hidden");
		this.$panel.find("tbody tr.docarchive-active").removeClass("docarchive-active");
		$(document).off(`keydown.docarchive-preview-${this.id}`);
	}

	listen_keys() {
		const namespace = `keydown.docarchive-preview-${this.id}`;
		$(document)
			.off(namespace)
			.on(namespace, (event) => {
				if (!this.$wrapper[0].isConnected) return $(document).off(namespace);
				if (!this.$panel.is(":visible") || is_typing(event.target)) return;
				if (document.querySelector(".modal.show")) return;
				const action = core.preview_key(event);
				if (!action) return;
				event.preventDefault();
				if (action === "close") return this.close_preview();
				const names = this.rows.map((doc) => doc.name);
				const next = core.step(names, this.previewing, action === "next" ? 1 : -1);
				if (next && next !== this.previewing) this.open_preview(next);
			});
	}

	render_preview() {
		const doc = this.doc(this.previewing);
		const files = preview_files(doc);
		const current = files[this.preview_file] || files[0];
		const form = frappe.utils.get_form_link(DOCUMENT, doc.name);
		const meta = [
			__(doc.document_type),
			frappe.datetime.str_to_user(doc.document_date),
			doc.document_number,
		]
			.filter(Boolean)
			.map((value) => `<span>${esc(value)}</span>`)
			.join("");
		const retain = doc.retain_until
			? `<div class="docarchive-preview-retain text-muted">${esc(
					__("Kept until {0}", [docarchive.format_retain_until(doc.retain_until)]),
				)}</div>`
			: "";
		const new_tab = current
			? `<a class="btn btn-default btn-xs" href="${esc(docarchive.file_url(current.file))}" target="_blank"
				rel="noopener">${frappe.utils.icon("external-link", "xs")} ${esc(__("Open in new tab"))}</a>`
			: "";
		const tabs =
			files.length > 1
				? `<div class="docarchive-preview-tabs" role="tablist">${files
						.map(
							(item, index) =>
								`<button class="btn btn-xs ${item === current ? "btn-primary" : "btn-default"}"
									role="tab" aria-selected="${item === current}" data-file-index="${index}"
									title="${esc(safe_file_name(item.file))}">${esc(item.label)}</button>`,
						)
						.join("")}</div>`
				: "";
		const $preview = this.$panel.find(".docarchive-preview");
		$preview.html(`<div class="docarchive-preview-head">
				<div class="docarchive-preview-title" title="${esc(doc.title || doc.name)}">${esc(doc.title || doc.name)}</div>
				<button class="btn btn-xs btn-default" data-action="close-preview"
					title="${esc(__("Close preview (Esc)"))}" aria-label="${esc(__("Close preview"))}">
					${frappe.utils.icon("close", "xs")}</button>
			</div>
			<div class="docarchive-preview-meta">${meta}${state_badge(doc)}</div>
			${retain}
			<div class="docarchive-preview-links">
				<a class="btn btn-default btn-xs" href="${esc(form)}">${esc(__("Open form"))}</a>
				${new_tab}
			</div>
			${tabs}
			<div class="docarchive-preview-stage"></div>`);
		this.render_stage($preview.find(".docarchive-preview-stage"), current);
	}

	render_stage($stage, current) {
		const request = (this.preview_request = {});
		if (!current) return $stage.html(this.stage_message(__("No file")));
		const url = current.file;
		const name = safe_file_name(url);
		const kind = core.viewer_kind(url);
		if (kind === "pdf") {
			return $stage.html(`<iframe class="docarchive-preview-frame" src="${esc(url)}" title="${esc(name)}"></iframe>`);
		}
		if (kind === "image") {
			return $stage.html(`<img class="docarchive-preview-image" src="${esc(url)}" alt="${esc(name)}">`);
		}
		if (kind === "html" && typeof docarchive.html_preview === "function") {
			$stage.html(this.stage_message(__("Loading preview...")));
			return Promise.resolve(docarchive.html_preview(url, name))
				.then((frame) => {
					if (request !== this.preview_request) return frame?.discard?.();
					$(frame).addClass("docarchive-preview-frame");
					$stage.empty().append(frame);
				})
				.catch(() => request === this.preview_request && $stage.html(this.download(url, name)));
		}
		$stage.html(this.download(url, name, kind === "html"));
	}

	stage_message(text) {
		return `<div class="docarchive-preview-empty text-muted">${esc(text)}</div>`;
	}

	download(url, name, html = false) {
		const href = local_file_url(url);
		const link = html
			? `<a class="btn btn-default btn-sm" href="${esc(docarchive.file_url(url))}" target="_blank"
				rel="noopener">${esc(__("Open in new tab"))}</a>`
			: href
				? `<a class="btn btn-default btn-sm" href="${esc(href)}" download>${esc(__("Download"))}</a>`
				: "";
		const note = html ? __("HTML files are only shown in the viewer.") : __("No preview available.");
		return `<div class="docarchive-preview-empty">
			<p class="docarchive-preview-file">${esc(name)}</p>
			<p class="text-muted">${esc(note)}</p>
			${link}
		</div>`;
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

function ask_details(party, files, on_saved) {
	let saved = false;
	const file_urls = files.map((file) => file.file_url);
	frappe.xcall(`${METHOD}.upload_defaults`).then((defaults) => {
		const dialog = new frappe.ui.Dialog({
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
					})),
				},
				{
					fieldname: "combine",
					fieldtype: "Check",
					label: __("One document with attachments"),
					description: __("The first file is the main file, the others become its attachments."),
					hidden: file_urls.length < 2,
				},
			],
			primary_action_label: __("Save"),
			primary_action(values) {
				const documents = (values.documents || []).map((row) => ({
					file_url: row.file_url,
					title: row.title,
					document_type: row.document_type,
					document_date: row.document_date,
				}));
				frappe
					.call({
						method: `${METHOD}.create_documents`,
						args: { ...party, documents, combine: values.combine },
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
	});
}

docarchive.upload_documents = function ({ counterparty_type, counterparty, files, on_saved }) {
	const uploaded = [];
	const uploader = new frappe.ui.FileUploader({
		allow_multiple: true,
		disable_file_browser: true,
		make_attachments_public: false,
		allow_toggle_private: false,
		files,
		on_success: (file) => uploaded.push({ file_url: file.file_url, file_name: file.file_name }),
	});
	uploader.dialog.onhide = () => {
		if (uploaded.length) ask_details({ counterparty_type, counterparty }, uploaded, on_saved);
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

