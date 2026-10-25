import { local_file_url, preview_kind } from "./triage_core.js";

export const MIN_YEAR = 1990;
export const STATES = ["open", "complete", "archived", "failed"];

const FILE_DATE =
	/(?<!\d)(?:(\d{4})([-_]?)(\d{2})\2(\d{2})|(\d{2})\.(\d{2})\.(\d{4}))(?!\d)/g;
const SEPARATORS = /[\s_-]+/g;
const EDGES = /^[\s.,;:_-]+|[\s.,;:_-]+$/g;

const pad = (value) => String(value).padStart(2, "0");

function valid_date(year, month, day, today) {
	const date = new Date(Date.UTC(year, month - 1, day));
	const fits =
		date.getUTCFullYear() === year &&
		date.getUTCMonth() === month - 1 &&
		date.getUTCDate() === day;
	return fits && year >= MIN_YEAR && year <= today.getFullYear() + 1;
}

export function stem(file_name) {
	const name = String(file_name || "").split("/").pop();
	const dot = name.lastIndexOf(".");
	return dot > 0 ? name.slice(0, dot) : name;
}

export function title_from_text(text) {
	return text.replace(SEPARATORS, " ").trim();
}

export function date_from_file_name(file_name, today = new Date()) {
	const base = stem(file_name);
	for (const match of base.matchAll(FILE_DATE)) {
		const [year, month, day] = match[1]
			? [match[1], match[3], match[4]]
			: [match[7], match[6], match[5]];
		if (!valid_date(+year, +month, +day, today)) continue;
		const rest = base.slice(0, match.index) + " " + base.slice(match.index + match[0].length);
		const title = title_from_text(rest).replace(EDGES, "");
		return { date: `${year}-${pad(month)}-${pad(day)}`, title: title || title_from_text(base) };
	}
	return { date: null, title: title_from_text(base) };
}

export const DELETED = "Deleted";

export function document_state(doc) {
	if (doc.archive_status === DELETED) return "deleted";
	if (doc.archive_status === "Archived") return "archived";
	if (doc.archive_status === "Failed") return "failed";
	return doc.missing && doc.missing.length ? "open" : "complete";
}

export const year_of = (doc) => (doc.document_date || "").slice(0, 4);
export const retain_year_of = (doc) => (doc.retain_until || "").slice(0, 4);
export const SORT_FIELDS = ["document_date", "retain_until"];

export function filter_documents(docs, filters = {}) {
	const query = (filters.search || "").trim().toLowerCase();
	const rows = docs.filter(
		(doc) =>
			(filters.show_deleted || doc.archive_status !== DELETED) &&
			(!query ||
				`${doc.title || ""} ${doc.document_number || ""}`.toLowerCase().includes(query)) &&
			(!filters.document_type || doc.document_type === filters.document_type) &&
			(!filters.year || year_of(doc) === filters.year) &&
			(!filters.retain_year || retain_year_of(doc) === filters.retain_year) &&
			(!filters.state || document_state(doc) === filters.state),
	);
	const field = SORT_FIELDS.includes(filters.sort_by) ? filters.sort_by : "document_date";
	const direction = filters.oldest_first ? 1 : -1;
	return rows.sort((a, b) => direction * (a[field] || "").localeCompare(b[field] || ""));
}

export function filter_options(docs) {
	const unique = (values) => [...new Set(values.filter(Boolean))];
	return {
		document_types: unique(docs.map((doc) => doc.document_type)).sort(),
		years: unique(docs.map(year_of)).sort().reverse(),
		retain_years: unique(docs.map(retain_year_of)).sort(),
	};
}

export const filter_key = (counterparty_type, counterparty) =>
	`docarchive.filters:${counterparty_type}:${counterparty}`;

const is_plain_object = (value) =>
	value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype;

export function load_filters(get_storage, key) {
	try {
		const value = JSON.parse(get_storage().getItem(key));
		return is_plain_object(value) ? value : {};
	} catch {
		return {};
	}
}

export function save_filters(get_storage, key, filters) {
	try {
		get_storage().setItem(key, JSON.stringify(filters));
	} catch {
		// storage can be unavailable or full
	}
}

export const BULK_ACTIONS = ["archive", "extend", "hold", "export"];

export function bulk_actions(doc, access = {}) {
	if (doc.archive_status === DELETED) return [];
	const archived = doc.archive_status === "Archived";
	return BULK_ACTIONS.filter((action) => {
		if (action === "archive") return Boolean(doc.eligible);
		if (!archived) return false;
		if (action === "hold") return Boolean(access.hold && !doc.legal_hold);
		return Boolean(access[action]);
	});
}

export const is_selectable = (doc, access) => bulk_actions(doc, access).length > 0;

export function selection_state(rows, selected, access) {
	const selectable = rows.filter((doc) => is_selectable(doc, access)).map((doc) => doc.name);
	const count = selectable.filter((name) => selected.has(name)).length;
	return {
		selectable,
		count,
		all: count > 0 && count === selectable.length,
		indeterminate: count > 0 && count < selectable.length,
	};
}

export function toggle_all(rows, selected, access, checked) {
	const { selectable } = selection_state(rows, selected, access);
	return checked ? new Set([...selected, ...selectable]) : new Set();
}

export function prune_selection(rows, selected, access) {
	return new Set(selection_state(rows, selected, access).selectable.filter((name) => selected.has(name)));
}

export function bulk_targets(rows, selected, access) {
	const targets = Object.fromEntries(BULK_ACTIONS.map((action) => [action, []]));
	for (const doc of rows) {
		if (!selected.has(doc.name)) continue;
		for (const action of bulk_actions(doc, access)) targets[action].push(doc.name);
	}
	return targets;
}

export function preview_key(event) {
	if (event.ctrlKey || event.metaKey || event.altKey) return null;
	if (event.key === "Escape") return "close";
	if (event.key === "ArrowDown" || event.key === "j") return "next";
	if (event.key === "ArrowUp" || event.key === "k") return "previous";
	return null;
}

export function step(names, current, delta) {
	if (!names.length) return null;
	const index = names.indexOf(current);
	if (index < 0) return names[0];
	return names[Math.min(Math.max(index + delta, 0), names.length - 1)];
}

export function viewer_kind(file_url) {
	const kind = preview_kind(file_url);
	if (kind !== "other") return kind;
	const name = (file_url || "").split(/[?#]/)[0].toLowerCase();
	return local_file_url(file_url) && /\.html?$/.test(name) ? "html" : "other";
}

export function export_defaults(party, docs) {
	const dates = docs.map((doc) => doc.document_date).filter(Boolean).sort();
	const types = [...new Set(docs.map((doc) => doc.document_type))];
	return {
		...party,
		...(dates.length ? { date_from: dates[0], date_to: dates[dates.length - 1] } : {}),
		...(types.length === 1 && types[0] ? { document_type: types[0] } : {}),
	};
}

export const PREVIEW_FIELDS = [
	["document_type", "Business Document Type"],
	["document_date", "Document Date"],
	["document_number", "Document Number"],
	["counterparty", "Counterparty"],
	["status", "Status"],
	["retain_until", "Retain Until (UTC)"],
	["legal_hold", "Legal Hold"],
];

export function preview_header(doc, { t, esc, value, actions = "" }) {
	const title = doc.title || doc.name;
	const cells = PREVIEW_FIELDS.map(([key, label]) => {
		const { text = "", html } = value(key, doc) || {};
		const shown = text ? html || esc(text) : `<span class="docarchive-empty">–</span>`;
		return `<div class="docarchive-field" data-field="${key}">
			<div class="docarchive-field-label">${esc(t(label))}</div>
			<div class="docarchive-field-value" title="${esc(text)}">${shown}</div>
		</div>`;
	}).join("");
	return `<div class="docarchive-preview-head">
			<div class="docarchive-preview-title" title="${esc(title)}">${esc(title)}</div>
			<div class="docarchive-preview-links">${actions}</div>
		</div>
		<div class="docarchive-preview-fields">${cells}</div>`;
}

export function party_label(name, id) {
	if (!id) return name || "";
	return name && name !== id ? `${name} · ${id}` : id;
}
