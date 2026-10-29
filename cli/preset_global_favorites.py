"""Private mixed-provider documents under the separate global favorites key.

Storage accepts a final document, never guesses its format or migrates the
legacy preset_favorites companion. Use global_favorites_document.plan_documents
for an explicit merge/replace preview before plan/apply; callers must reject
blocked previews. The store accepts only the selected final document, not a
planner result or runtime mapping decision. Both stores use the same
config lock, exact byte revision and full private backup, preserving other keys.
"""

import preset_favorites as _storage

KEY = "preset_global_favorites"
StorageError = _storage.StorageError
UpdatePlan = _storage.UpdatePlan


class Store(_storage.Store):
    _key = KEY

    def __init__(self, path, *, preset_names, resolve_preset, validate_document=None):
        if validate_document is None:
            from global_favorites_document import validate_document
        super().__init__(path, preset_names=preset_names, resolve_preset=resolve_preset,
                         validate_document=validate_document)
