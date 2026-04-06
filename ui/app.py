import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from config.settings import Settings
from core.db_manager import DatabaseManager
from core.api_client import ApiClient
from core.cache_manager import CacheManager
from utils.health_check import HealthChecker
from utils.schema_mapper import EndpointRegistry
from utils.logger import setup_logger
from ui.components.widgets import (
    render_cache_badge,
    render_json_viewer,
    render_table_view,
    render_image_gallery,
    render_search_results,
    render_stats_panel,
)

st.set_page_config(
    page_title="IMDB Cache UI",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def initialize_app():
    settings = Settings()
    logger = setup_logger(
        log_file=settings.log_file,
        level=settings.log_level,
        json_format=settings.log_json,
    )

    checker = HealthChecker(settings)
    healthy = checker.run_all()
    report = checker.get_report()

    if not healthy:
        return settings, None, None, None, report

    db_manager = DatabaseManager(settings)
    db_manager.ensure_database_exists()
    db_manager.initialize_engine()
    db_manager.test_connection()
    db_manager.create_tables()

    api_client = ApiClient(settings)
    cache_manager = CacheManager(settings, db_manager, api_client)
    registry = EndpointRegistry()

    return settings, db_manager, api_client, cache_manager, report


def render_sidebar(cache_manager, registry):
    st.sidebar.title("🎬 IMDB Cache UI")
    st.sidebar.caption("v1.0.0")

    st.sidebar.divider()
    st.sidebar.subheader("Cache Stats")
    stats = cache_manager.get_stats()
    render_stats_panel(stats)

    st.sidebar.divider()
    st.sidebar.subheader("Cache Management")
    if st.sidebar.button("🗑️ Clear All Expired", use_container_width=True):
        st.toast("Expired entries cleaned up", icon="✅")

    st.sidebar.divider()
    st.sidebar.subheader("Endpoints")
    endpoints = registry.list_endpoints()
    for ep in endpoints:
        st.sidebar.caption(f"`{ep['path']}`")


def render_search_tab(cache_manager, registry):
    st.subheader("🔍 Search IMDB")

    search_type = st.radio("Search Type", ["Title", "Name"], horizontal=True)

    query = st.text_input(
        "Search Query",
        placeholder="Enter movie title or person name...",
        key="search_query",
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        force_refresh = st.checkbox("Force Refresh", value=False)

    if st.button("🔎 Search", type="primary", use_container_width=True):
        if not query.strip():
            st.warning("Please enter a search query")
            return

        endpoint = "search_title" if search_type == "Title" else "search_name"

        with st.spinner(f"Searching for '{query}'..."):
            try:
                result, status = cache_manager.get(
                    endpoint=endpoint,
                    query=query,
                    force_refresh=force_refresh,
                )

                col_status, col_meta = st.columns([1, 4])
                with col_status:
                    render_cache_badge(status)

                if result.get("error") == "not_found":
                    st.error("No results found")
                    return

                results = result.get("results", [])
                if results:
                    render_search_results(results)
                else:
                    st.info("No results returned")

                if result.get("errorMessage"):
                    st.warning(f"API Warning: {result['errorMessage']}")

            except Exception as e:
                st.error(f"Search failed: {e}")


def render_detail_tab(cache_manager, registry):
    st.subheader("📋 Detail Lookup")

    detail_type = st.radio(
        "Lookup Type",
        ["Title Detail", "Title Rating", "Name Detail", "Name Filmography"],
        horizontal=True,
    )

    endpoint_map = {
        "Title Detail": "titles_detail",
        "Title Rating": "titles_rating",
        "Name Detail": "names_detail",
        "Name Filmography": "names_filmography",
    }

    endpoint_name = endpoint_map[detail_type]
    resource_id = st.text_input(
        "IMDB ID",
        placeholder="e.g. tt0111161 or nm0000093",
        key="detail_id",
    )

    force_refresh = st.checkbox("Force Refresh", value=False, key="detail_refresh")

    if st.button("🔎 Lookup", type="primary", use_container_width=True):
        if not resource_id.strip():
            st.warning("Please enter an IMDB ID")
            return

        with st.spinner(f"Looking up {resource_id}..."):
            try:
                result, status = cache_manager.get(
                    endpoint=endpoint_name,
                    resource_id=resource_id.strip(),
                    force_refresh=force_refresh,
                )

                col_status, col_meta = st.columns([1, 4])
                with col_status:
                    render_cache_badge(status)

                if result.get("error") == "not_found":
                    st.error(f"Resource '{resource_id}' not found")
                    return

                view_mode = st.radio(
                    "View Mode",
                    ["Pretty", "Table", "Raw JSON"],
                    horizontal=True,
                    key="detail_view",
                )

                if view_mode == "Pretty":
                    _render_pretty_detail(result, detail_type)
                elif view_mode == "Table":
                    render_table_view(result)
                else:
                    render_json_viewer(result)

            except Exception as e:
                st.error(f"Lookup failed: {e}")


def _render_pretty_detail(data: dict, detail_type: str):
    if detail_type == "Title Detail":
        col1, col2 = st.columns([1, 2])
        with col1:
            if data.get("image"):
                st.image(data["image"], use_container_width=True)
        with col2:
            st.header(data.get("title", "Unknown Title"))
            if data.get("year"):
                st.caption(f"📅 {data['year']}")
            if data.get("imDbRating"):
                st.caption(f"⭐ {data['imDbRating']} / 10")
            if data.get("runtimeMins"):
                st.caption(f"⏱️ {data['runtimeMins']} minutes")
            if data.get("genres"):
                st.caption(f"🎭 {', '.join(data['genres']) if isinstance(data['genres'], list) else data['genres']}")
            if data.get("plot"):
                st.markdown(f"**Plot:** {data['plot']}")
            if data.get("directors"):
                st.caption(f"🎬 Directors: {data['directors']}")
            if data.get("stars"):
                st.caption(f"🌟 Stars: {data['stars']}")

    elif detail_type == "Title Rating":
        st.header(data.get("title", "Unknown"))
        cols = st.columns(4)
        ratings = [
            ("IMDB", data.get("imDb")),
            ("Metacritic", data.get("metacritic")),
            ("TMDb", data.get("theMovieDb")),
            ("Rotten Tomatoes", data.get("rottenTomatoes")),
        ]
        for i, (label, value) in enumerate(ratings):
            with cols[i]:
                st.metric(label, value or "N/A")

    elif detail_type == "Name Detail":
        col1, col2 = st.columns([1, 2])
        with col1:
            if data.get("image"):
                st.image(data["image"], use_container_width=True)
        with col2:
            st.header(data.get("name", "Unknown"))
            if data.get("birthDate"):
                st.caption(f"🎂 Born: {data['birthDate']}")
            if data.get("birthPlace"):
                st.caption(f"📍 {data['birthPlace']}")
            if data.get("heightCm"):
                st.caption(f"📏 {data['heightCm']} cm")

    elif detail_type == "Name Filmography":
        st.header(data.get("name", "Unknown"))
        jobs = data.get("jobs", {})
        if isinstance(jobs, dict):
            for job_title, titles in jobs.items():
                with st.expander(f"{job_title} ({len(titles) if isinstance(titles, list) else 0})", expanded=False):
                    if isinstance(titles, list):
                        for t in titles[:20]:
                            st.caption(f"• {t.get('title', 'Unknown')} ({t.get('year', '')})")
                        if len(titles) > 20:
                            st.caption(f"... and {len(titles) - 20} more")


def render_cache_tab(cache_manager):
    st.subheader("💾 Cache Management")

    stats = cache_manager.get_stats()
    render_stats_panel(stats)

    st.divider()
    st.subheader("Invalidate Cache")

    col1, col2 = st.columns(2)
    with col1:
        endpoint_to_clear = st.text_input("Endpoint (leave blank for all)", placeholder="e.g. titles_detail")
    with col2:
        resource_to_clear = st.text_input("Resource ID", placeholder="e.g. tt0111161")

    if st.button("🗑️ Invalidate", type="primary", use_container_width=True):
        if endpoint_to_clear and resource_to_clear:
            count = cache_manager.db_storage.invalidate_by_endpoint(
                endpoint_to_clear
            )
            st.success(f"Invalidated {count} entries for '{endpoint_to_clear}'")
        elif endpoint_to_clear:
            count = cache_manager.invalidate_endpoint(endpoint_to_clear)
            st.success(f"Invalidated {count} entries for endpoint '{endpoint_to_clear}'")
        else:
            st.warning("Please specify at least an endpoint")


def render_main():
    settings, db_manager, api_client, cache_manager, report = initialize_app()

    if not cache_manager:
        st.error("⚠️ Application failed to initialize. Check the logs.")
        st.json(report)
        st.stop()

    st.title("🎬 IMDB Cache UI")

    tab1, tab2, tab3 = st.tabs(["🔍 Search", "📋 Detail Lookup", "💾 Cache Management"])

    with tab1:
        render_search_tab(cache_manager, EndpointRegistry())

    with tab2:
        render_detail_tab(cache_manager, EndpointRegistry())

    with tab3:
        render_cache_tab(cache_manager)


if __name__ == "__main__":
    render_main()
