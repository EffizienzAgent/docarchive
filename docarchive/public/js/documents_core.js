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

export function document_state(doc) {
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

export function load_filters(storage, key) {
	try {
		return JSON.parse(storage.getItem(key)) || {};
	} catch {
		return {};
	}
}

export function save_filters(storage, key, filters) {
	try {
		storage.setItem(key, JSON.stringify(filters));
	} catch {
		// storage can be unavailable or full
	}
}
