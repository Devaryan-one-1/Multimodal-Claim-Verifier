"""Distinct labels for Streamlit's string-based resource selectors."""


def resource_labels(entries):
    entries=list(entries)
    ids=[entry["id"] for entry in entries]
    width=8
    while len({value[:width] for value in ids})<len(ids) and width<max(map(len,ids),default=8):
        width+=4
    return {entry["id"]:f"{entry['name']} · {entry['id'][:width]}" for entry in entries}
