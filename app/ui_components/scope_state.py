"""Persistent selection draft, separate from Streamlit's disposable widgets."""

import streamlit as st


def draft_for(key: str, previous: dict) -> dict:
    storage = "agent:scope-draft:" + key
    if storage not in st.session_state:
        folders = list(previous.get("folder_ids", []))
        if previous.get("folder_id"):
            folders.append(previous["folder_id"])
        if previous.get("uncategorized"):
            folders.append("uncategorized")
        st.session_state[storage] = {
            "all_documents": not previous or previous.get("kind") == "all",
            "document_ids": list(previous.get("document_ids", [])),
            "folders": list(dict.fromkeys(folders)),
            "excluded_document_ids": list(previous.get("excluded_document_ids", [])),
            "chat": previous.get("kind") == "chat",
            "include_samples": previous.get("include_samples", False),
        }
    return st.session_state[storage]


def selected_scope(key: str, previous: dict) -> dict | None:
    draft = draft_for(key, previous)
    if draft["chat"]:
        return {"kind": "chat"}
    if draft.get("all_documents", False):
        return {
            "kind": "all",
            "excluded_document_ids": draft["excluded_document_ids"],
            "include_samples": draft["include_samples"],
        }
    if not draft["document_ids"] and not draft["folders"]:
        return None
    return {
        "kind": "mixed",
        "document_ids": draft["document_ids"],
        "folder_ids": [i for i in draft["folders"] if i != "uncategorized"],
        "uncategorized": "uncategorized" in draft["folders"],
        "excluded_document_ids": draft["excluded_document_ids"],
        "include_samples": draft["include_samples"],
    }


def remember(key: str, field: str, widget: str) -> None:
    draft_for(key, {})[field] = st.session_state[widget]


def clear_scope(key: str) -> None:
    draft = draft_for(key, {})
    draft["all_documents"] = False
    for field in ("document_ids", "folders", "excluded_document_ids"):
        draft[field] = []


def folder_of(document: dict) -> str:
    return document.get("category_id") or "uncategorized"


def effective_ids(draft: dict, documents: list[dict]) -> set[str]:
    covered = {
        d["id"]
        for d in documents
        if draft.get("all_documents", False) or folder_of(d) in draft["folders"]
    }
    return (set(draft["document_ids"]) | covered) - set(draft["excluded_document_ids"])


def toggle_documents(key: str, identifiers: list[str], covered: list[str], checked: bool) -> None:
    draft = draft_for(key, {})
    direct, excluded = set(draft["document_ids"]), set(draft["excluded_document_ids"])
    if draft.get("all_documents", False):
        if checked:
            excluded.difference_update(identifiers)
        else:
            excluded.update(identifiers)
        draft["excluded_document_ids"] = sorted(excluded)
        return
    if checked:
        direct.update(identifiers)
        excluded.difference_update(identifiers)
    else:
        direct.difference_update(identifiers)
        excluded.update(set(identifiers) & set(covered))
    draft["document_ids"], draft["excluded_document_ids"] = sorted(direct), sorted(excluded)


def toggle_folders(key: str, identifiers: list[str], documents: list[dict], checked: bool) -> None:
    draft = draft_for(key, {})
    if draft.get("all_documents", False):
        draft["document_ids"] = sorted(effective_ids(draft, documents))
        draft["excluded_document_ids"] = []
        draft["all_documents"] = False
    folders = set(draft["folders"])
    if checked:
        folders.update(identifiers)
        restored = {d["id"] for d in documents if folder_of(d) in identifiers}
        draft["excluded_document_ids"] = [
            i for i in draft["excluded_document_ids"] if i not in restored
        ]
    else:
        folders.difference_update(identifiers)
        # Cancelling the group also unchecks its currently selected individual files.
        removed = {d["id"] for d in documents if folder_of(d) in identifiers}
        draft["document_ids"] = [i for i in draft["document_ids"] if i not in removed]
        draft["excluded_document_ids"] = [
            i for i in draft["excluded_document_ids"] if i not in removed
        ]
    draft["folders"] = sorted(folders)
