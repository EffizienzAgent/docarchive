(() => {
	const ROW = ".list-row-container";
	const ROW_CHECKBOX = ".list-row-checkbox";
	const ROW_ANCHOR = ".list-row .level-right";

	const settings = (frappe.listview_settings["Communication"] ||= {});
	const onload = settings.onload;
	const refresh = settings.refresh;

	const no_link = () => "";

	const svg = (paths) =>
		`<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
	const ICONS = {
		archive: svg('<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>'),
		discard: svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
	};
	const ACTION_LABELS = { archive: "Archive", discard: "Discard", unignore: "Archive" };

	function state_html(badge, status) {
		const esc = frappe.utils.escape_html;
		const label = badge.label_html.split(" · ")[0];
		const doc = status.business_document;
		const action = badge.actions.find((name) => name !== "unignore") || badge.actions[0];
		const parts = [];
		if (doc) {
			parts.push(
				`<a class="docarchive-icon ${badge.color}" href="${esc(frappe.utils.get_form_link("Business Document", doc))}" title="${label}: ${esc(doc)}">${ICONS.archive}</a>`,
			);
		}
		if (action) {
			const title = doc ? __(ACTION_LABELS[action]) : `${__(ACTION_LABELS[action])} (${label})`;
			parts.push(
				`<button class="docarchive-icon docarchive-action ${doc ? "" : badge.color}" data-docarchive-action="${action}" title="${esc(title)}">${ICONS[action === "discard" ? "discard" : "archive"]}</button>`,
			);
		}
		if (!parts.length) parts.push(`<span class="docarchive-icon ${badge.color}" title="${label}">${ICONS.archive}</span>`);
		return parts.join("");
	}

	function decorate_row(listview, row, name, status) {
		const $slot = $('<span class="docarchive-state"></span>').on("click", (event) => event.stopPropagation());
		$(row).find(ROW_ANCHOR).first().prepend($slot);
		const badge = status && docarchive.mail_core.archive_badge(status, { t: __, link: no_link });
		if (!badge) return;
		$slot.html(state_html(badge, status));
		docarchive.bind_state_actions($slot, name, status, () => listview.refresh());
	}

	function decorate(listview) {
		const token = (listview.docarchive_token = (listview.docarchive_token || 0) + 1);
		const names = (listview.data || []).map((doc) => doc.name);
		docarchive
			.archive_states(names)
			.then((states) => {
				if (token !== listview.docarchive_token) return;
				listview.$result.find(ROW).each((_, row) => {
					const name = $(row).find(ROW_CHECKBOX).attr("data-name");
					if (!$(row).find(".docarchive-state").length) {
						decorate_row(listview, row, name, states[name]);
					}
				});
			})
			.catch((error) => console.debug("docarchive list", error));
	}

	settings.onload = function (listview) {
		onload && onload.apply(this, arguments);
		if (!frappe.model.can_create("Business Document")) return;
		listview.page.add_actions_menu_item(__("Archive"), () => {
			const names = listview.get_checked_items(true);
			if (names.length) docarchive.archive_communications(names, () => listview.refresh());
		});
	};

	settings.refresh = function (listview) {
		try {
			refresh && refresh.apply(this, arguments);
			decorate(listview);
		} catch (error) {
			console.debug("docarchive list", error);
		}
	};
})();
