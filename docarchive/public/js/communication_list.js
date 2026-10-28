(() => {
	const ROW = ".list-row-container";
	const ROW_CHECKBOX = ".list-row-checkbox";
	const ROW_ANCHOR = ".list-row .level-right";

	const settings = (frappe.listview_settings["Communication"] ||= {});
	const onload = settings.onload;
	const refresh = settings.refresh;

	const no_link = () => "";

	function state_html(badge, status) {
		const esc = frappe.utils.escape_html;
		const label = badge.label_html.split(" · ")[0];
		const doc = status.business_document;
		const pill = doc
			? `<a class="indicator-pill ${badge.color}" href="${esc(frappe.utils.get_form_link("Business Document", doc))}" title="${esc(doc)}">${label}</a>`
			: `<span class="indicator-pill ${badge.color}">${label}</span>`;
		const archive = badge.actions.includes("archive")
			? `<button class="btn btn-xs btn-link" data-docarchive-action="archive">${esc(__("Archive"))}</button>`
			: "";
		const discard = badge.actions.includes("discard")
			? `<button class="btn btn-xs btn-link" data-docarchive-action="discard">${esc(__("Discard"))}</button>`
			: "";
		return `<span class="docarchive-state">${pill}${archive}${discard}</span>`;
	}

	function decorate_row(listview, row, name, status) {
		const badge = docarchive.mail_core.archive_badge(status, { t: __, link: no_link });
		if (!badge) return;
		const $state = $(state_html(badge, status));
		$state.on("click", (event) => event.stopPropagation());
		$(row).find(ROW_ANCHOR).first().prepend($state);
		docarchive.bind_state_actions($state, name, status, () => listview.refresh());
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
					const status = states[name];
					if (status && !$(row).find(".docarchive-state").length) {
						decorate_row(listview, row, name, status);
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
