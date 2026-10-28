from importlib import import_module

_EXPORTS = {
	"archive_originals": "inbox",
	"candidates": "inbox",
	"count_candidates": "inbox",
	"ignore": "inbox",
	"unignore": "inbox",
	"capture": "originals",
	"thread_pending": "thread",
}


def __getattr__(name):
	if name not in _EXPORTS:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
	return getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
