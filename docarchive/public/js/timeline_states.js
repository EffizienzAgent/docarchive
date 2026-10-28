const TIMELINE_ITEM = '.timeline-item[data-doctype="Communication"]';
const ITEM_ACTIONS = ".timeline-message-box .actions";
const ITEM_ACTIONS_ANCHOR = ".custom-actions";
const CHECKED = "docarchiveChecked";
const DEBOUNCE = 100;

const link = (name) => {
	const esc = frappe.utils.escape_html;
	return `<a href="${esc(frappe.utils.get_form_link("Business Document", name))}">${esc(name)}</a>`;
};

const ACTION_LABELS = () => ({
	archive: __("Archive"),
	discard: __("Discard"),
	unignore: __("Unignore"),
});

function action_html(action, badge) {
	const label = frappe.utils.escape_html(ACTION_LABELS()[action]);
	const style = action === "archive" && badge.color === "yellow" ? "btn-primary" : "btn-link";
	return `<button class="btn btn-xs ${style}" data-docarchive-action="${action}">${label}</button>`;
}

function decorate_item(frm, item, status) {
	const badge = docarchive.mail_core.archive_badge(status, { t: __, link });
	if (!badge) return;
	const html = `<span class="docarchive-state">
		<span class="indicator-pill ${badge.color} ellipsis">${badge.label_html}</span>
		${badge.actions.map((action) => action_html(action, badge)).join("")}
	</span>`;
	const $item = $(item);
	$item.find(ITEM_ACTIONS).find(ITEM_ACTIONS_ANCHOR).before(html);
	docarchive.bind_state_actions($item, item.dataset.name, status, () => {
		$item.find(".docarchive-state").remove();
		delete item.dataset[CHECKED];
		docarchive_decorate_timeline(frm);
	});
}

function docarchive_decorate_timeline(frm) {
	try {
		const wrapper = frm.timeline?.timeline_items_wrapper;
		if (!wrapper) return;
		const items = wrapper.find(TIMELINE_ITEM).filter((_, item) => !(CHECKED in item.dataset)).get();
		if (!items.length) return;
		items.forEach((item) => (item.dataset[CHECKED] = ""));
		docarchive
			.archive_states(items.map((item) => item.dataset.name))
			.then((states) => {
				items.forEach((item) => {
					const status = states[item.dataset.name];
					if (status && item.isConnected) decorate_item(frm, item, status);
				});
			})
			.catch((error) => console.debug("docarchive timeline", error));
	} catch (error) {
		console.debug("docarchive timeline", error);
	}
}

function docarchive_observe_timeline(frm) {
	const target = frm.timeline?.timeline_items_wrapper?.get(0);
	if (!target || frm.docarchive_timeline_target === target) return;
	frm.docarchive_timeline_observer?.disconnect();
	frm.docarchive_timeline_target = target;
	let timer = null;
	frm.docarchive_timeline_observer = new MutationObserver(() => {
		clearTimeout(timer);
		timer = setTimeout(() => docarchive_decorate_timeline(frm), DEBOUNCE);
	});
	frm.docarchive_timeline_observer.observe(target, { childList: true });
}

$(document).on("form-refresh", (event, frm) => {
	try {
		docarchive_observe_timeline(frm);
		docarchive_decorate_timeline(frm);
	} catch (error) {
		console.debug("docarchive timeline", error);
	}
});

$(document).on("form-unload", (event, frm) => {
	frm.docarchive_timeline_observer?.disconnect();
	frm.docarchive_timeline_observer = frm.docarchive_timeline_target = null;
});
