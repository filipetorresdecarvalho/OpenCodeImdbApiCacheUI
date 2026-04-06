import streamlit as st


def render_cache_badge(status: str):
    badges = {
        "hit": ("✅", "Cache Hit", "green"),
        "fs_hit": ("💾", "FS Cache Hit", "blue"),
        "miss": ("🔄", "Fresh from API", "orange"),
        "coalesced": ("🔗", "Coalesced Request", "gray"),
        "fresh": ("✨", "Fresh Data", "green"),
        "not_found": ("❌", "Not Found", "red"),
        "expired": ("⏰", "Expired", "yellow"),
    }
    icon, label, color = badges.get(status, ("❓", status, "gray"))
    st.markdown(
        f'<span style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;'
        f'border-radius:12px;background-color:{color}22;color:{color};'
        f'font-size:0.85rem;font-weight:600;border:1px solid {color}44;">'
        f'{icon} {label}</span>',
        unsafe_allow_html=True,
    )


def render_json_viewer(data: dict, expanded: bool = False):
    import json
    json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    st.code(json_str, language="json")


def render_table_view(data: dict):
    import pandas as pd

    flat_data = _flatten_dict(data)
    if not flat_data:
        st.warning("No tabular data to display")
        return

    df = pd.DataFrame([flat_data])
    st.dataframe(df, use_container_width=True, hide_index=True)


def _flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, new_key, sep=sep))
        elif isinstance(v, list):
            items[new_key] = str(v)
        else:
            items[new_key] = v
    return items


def render_image_gallery(image_paths: list, cache_dir: str = "cache/imdbapi"):
    from pathlib import Path

    if not image_paths:
        return

    cols = st.columns(min(len(image_paths), 4))
    for i, img_path in enumerate(image_paths):
        full_path = Path(cache_dir) / img_path
        if full_path.exists():
            with cols[i % 4]:
                st.image(str(full_path), use_container_width=True)
        else:
            with cols[i % 4]:
                st.caption(f"📁 {img_path}")


def render_search_results(results: list):
    if not results:
        st.info("No results found")
        return

    for item in results:
        with st.container(border=True):
            col1, col2 = st.columns([1, 3])
            with col1:
                if item.get("image") or item.get("imageUrl"):
                    st.image(item.get("image") or item.get("imageUrl"), width=80)
                else:
                    st.markdown("🎬", unsafe_allow_html=True)
            with col2:
                title = item.get("title") or item.get("name") or "Unknown"
                st.markdown(f"**{title}**")
                desc_parts = []
                if item.get("year"):
                    desc_parts.append(str(item["year"]))
                if item.get("description"):
                    desc_parts.append(item["description"])
                if desc_parts:
                    st.caption(" | ".join(desc_parts))
                if item.get("id"):
                    st.caption(f"`{item['id']}`")


def render_stats_panel(stats: dict):
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Entries", stats.get("total_entries", 0))
    with col2:
        st.metric("Valid Entries", stats.get("valid_entries", 0))
    with col3:
        st.metric("Expired Entries", stats.get("expired_entries", 0))
