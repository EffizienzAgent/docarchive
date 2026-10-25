const ViewerCore = (() => {
	const KEYS = {
		j: "next",
		ArrowDown: "next",
		k: "previous",
		ArrowUp: "previous",
		Enter: "open",
		"/": "search",
		Escape: "escape",
		ArrowRight: "next_file",
		ArrowLeft: "previous_file",
		"?": "help",
	};
	const TEXT_INPUTS = ["checkbox", "radio", "button", "submit", "reset"];

	function typing(target) {
		if (!target) return false;
		if (target.isContentEditable) return true;
		const tag = (target.tagName || "").toUpperCase();
		if (tag === "TEXTAREA" || tag === "SELECT") return true;
		return tag === "INPUT" && !TEXT_INPUTS.includes((target.type || "text").toLowerCase());
	}

	function control(target) {
		const tag = ((target && target.tagName) || "").toUpperCase();
		return tag === "A" || tag === "BUTTON" || tag === "INPUT" || tag === "SELECT";
	}

	function action(event, context = {}) {
		if (event.ctrlKey || event.metaKey || event.altKey) return null;
		const name = KEYS[event.key] || null;
		if (context.help) return name === "escape" || name === "help" ? "close_help" : null;
		if (context.typing) return name === "escape" ? "escape" : null;
		if (name === "open" && context.control) return null;
		return name;
	}

	function escape(context = {}) {
		if (context.help) return "close_help";
		if (context.search) return "clear_search";
		if (context.typing) return "blur";
		if (context.preview) return "close_preview";
		return null;
	}

	function move(ids, current, delta) {
		if (!ids.length) return null;
		const index = ids.indexOf(current);
		if (index < 0) return delta < 0 ? ids[ids.length - 1] : ids[0];
		return ids[Math.min(Math.max(index + delta, 0), ids.length - 1)];
	}

	function cycle(count, index, delta) {
		if (count <= 0) return 0;
		return (((index + delta) % count) + count) % count;
	}

	const typeOf = (doc) => doc.type_label || doc.type || "";
	function party(doc) {
		const value = doc.counterparty;
		if (!value) return "";
		if (value.label) return value.label;
		return value.name && value.name !== value.id ? `${value.name} (${value.id})` : value.id || "";
	}

	const partyType = (doc) => (doc.counterparty && (doc.counterparty.type_label || doc.counterparty.doctype)) || "";

	function meta(doc, L) {
		return [
			["type", L.type, typeOf(doc)],
			["date", L.date, doc.date || ""],
			["number", L.number, doc.number || ""],
			["counterparty", L.counterparty, party(doc)],
			["counterparty_type", L.counterparty_type, partyType(doc)],
			["status", L.status, doc.verified ? L.verified : L.problem],
			["retain_until", L.retain_until, doc.retain_until || ""],
			["legal_hold", L.legal_hold, doc.legal_hold ? L.yes : ""],
			["own_company", L.own_company, doc.own_company || ""],
			["lock_mode", L.lock_mode, doc.lock_mode || ""],
		];
	}

	function outcomeOf(entry, id) {
		const row = entry.documents.find((item) => item.id === id) || {};
		return [row.outcome, row.reason].filter(Boolean).join(": ");
	}

	function auditRows(entries, filters = {}) {
		const term = (filters.search || "").trim().toLowerCase();
		return entries
			.filter((entry) =>
				(!filters.action || entry.action === filters.action) &&
				(!term || [entry.user, entry.action, ...entry.documents.map((row) => row.id)].some((value) => (value || "").toLowerCase().includes(term))))
			.slice()
			.sort((a, b) => b.at.localeCompare(a.at));
	}

	function entriesOf(doc, entries) {
		const byId = new Map(entries.map((entry) => [entry.id, entry]));
		return (doc.audit || []).map((id) => byId.get(id)).filter(Boolean).sort((a, b) => b.at.localeCompare(a.at));
	}

	function sentAt(doc) {
		return doc.correspondence_at || doc.date || "";
	}

	const threadId = (doc) => doc.thread_root || doc.thread_key;

	function threadOf(doc, docs) {
		if (!threadId(doc)) return [];
		return docs
			.filter((other) => threadId(other) === threadId(doc))
			.sort((a, b) => sentAt(b).localeCompare(sentAt(a)) || b.id.localeCompare(a.id));
	}

	function size(bytes) {
		if (bytes === null || bytes === undefined) return "";
		if (bytes < 1024) return `${bytes} B`;
		const units = ["KB", "MB", "GB"];
		let value = bytes / 1024;
		let unit = 0;
		while (value >= 1024 && unit < units.length - 1) {
			value /= 1024;
			unit += 1;
		}
		return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
	}

	return { action, escape, move, cycle, typing, control, meta, typeOf, party, partyType, size, outcomeOf, auditRows, entriesOf, threadOf };
})();

if (typeof module !== "undefined") module.exports = ViewerCore;
