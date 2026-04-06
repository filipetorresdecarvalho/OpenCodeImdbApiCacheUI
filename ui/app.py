"""
Streamlit UI for IMDB Cache application.

Main entry point with three tabs:
1. Search - Title/Name search with results
2. Detail Lookup - Get full details by IMDB ID
3. Cache Management - View and manage cache

Features:
- Cache hit/miss badges with detailed metadata
- Multiple view modes (Pretty, Table, Raw JSON)
- Error recovery with detailed logging
- Sidebar with cache statistics
- Safe error handling throughout
"""
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
from core.queue import RateLimitedQueue
from utils.health_check import HealthChecker
from utils.schema_mapper import EndpointRegistry
from utils.logger import setup_logger, logger
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
    """Initialize application with health checks and database setup.

    Returns:
        Tuple of (settings, db_manager, api_client, cache_manager, health_report)
        If health check fails, cache_manager will be None
    """
    try:
        # Load settings from environment
        settings = Settings()

        # Setup logger
        logger_instance = setup_logger(
            log_file=settings.log_file,
            error_file=settings.log_error_file,
            level=settings.log_level,
            json_format=settings.log_json,
        )

        # Run startup health checks
        checker = HealthChecker(settings)
        healthy = checker.run_all()
        report = checker.get_report()

        if not healthy:
            logger.error("Health check failed, app cannot start")
            return settings, None, None, None, report

        # Initialize database
        logger.info("Initializing database...")
        db_manager = DatabaseManager(settings)

        # Check for local database first
        local_found, local_service, local_info = db_manager.detect_local_database()
        if local_found:
            logger.info(f"Using local database: {local_info}")
            # Update settings to use local database if detected
            # (The health check already verified local connection works)

        if not db_manager.ensure_database_exists():
            logger.error("Failed to create/verify database")
            return settings, None, None, None, report

        if not db_manager.initialize_engine():
            logger.error("Failed to initialize database engine")
            return settings, None, None, None, report

        if not db_manager.test_connection():
            logger.error("Database connection test failed")
            return settings, None, None, None, report

        if not db_manager.create_tables():
            logger.error("Failed to create database tables")
            return settings, None, None, None, report

        logger.info("Database initialization successful")

        # Initialize API client
        logger.info("Initializing API client...")
        api_client = ApiClient(settings)

        # Initialize rate-limited queue
        logger.info(f"Initializing rate-limited queue: {settings.imdb_rate_limit} req/sec")
        queue = RateLimitedQueue(
            max_requests_per_second=settings.imdb_rate_limit,
            max_retries=settings.imdb_max_retries,
        )
        queue.start()

        # Initialize cache manager with queue
        logger.info("Initializing cache manager...")
        cache_manager = CacheManager(settings, db_manager, api_client, queue)

        logger.info("Application initialization complete")
        return settings, db_manager, api_client, cache_manager, report

    except Exception as e:
        logger.error(
            f"Failed to initialize application: {e}",
            exc_info=True,
            extra={"error_code": "APP_INIT_FAILED"},
        )
        return None, None, None, None, {
            "healthy": False,
            "error": str(e),
        }


def render_sidebar(cache_manager, registry):
    """Render the sidebar with cache stats and management options.

    Args:
        cache_manager: CacheManager instance
        registry: EndpointRegistry instance
    """
    try:
        st.sidebar.title("🎬 IMDB Cache UI")
        st.sidebar.caption("v1.0.0 | Free IMDB API | Rate: 1 req/sec")

        st.sidebar.divider()
        st.sidebar.subheader("📊 Cache Statistics")

        try:
            stats = cache_manager.get_stats()
            render_stats_panel(stats)
        except Exception as e:
            logger.error(f"Failed to render stats: {e}", exc_info=True)
            st.sidebar.warning(f"Error loading stats: {e}")

        st.sidebar.divider()
        st.sidebar.subheader("🛠️ Cache Management")

        if st.sidebar.button("🗑️ Clear All Expired", use_container_width=True):
            try:
                # Get all entries and clean up expired ones
                # This is a placeholder - implement full cleanup logic
                st.toast("Expired entries queued for cleanup", icon="✅")
                logger.info("User initiated expired cache cleanup")
            except Exception as e:
                logger.error(f"Failed to clear expired cache: {e}", exc_info=True)
                st.toast(f"Error: {e}", icon="❌")

        st.sidebar.divider()
        st.sidebar.subheader("📝 Available Endpoints")

        try:
            endpoints = registry.list_endpoints()
            for ep in endpoints:
                st.sidebar.caption(f"📌 {ep['name']}")
                st.sidebar.caption(f"   └─ {ep['path']}")
        except Exception as e:
            logger.error(f"Failed to list endpoints: {e}", exc_info=True)
            st.sidebar.warning("Error loading endpoints")

    except Exception as e:
        logger.error(
            f"Error rendering sidebar: {e}",
            exc_info=True,
            extra={"error_code": "SIDEBAR_RENDER_ERROR"},
        )
        st.sidebar.error(f"Sidebar error: {e}")


def render_search_tab(cache_manager, registry):
    """Render the search tab for title and name search.

    Args:
        cache_manager: CacheManager instance
        registry: EndpointRegistry instance
    """
    try:
        st.subheader("🔍 Search IMDB")

        # Search type selector
        search_type = st.radio(
            "Search Type",
            ["Title", "Name"],
            horizontal=True,
            help="Search for movies/titles or people/actors",
        )

        # Search input
        query = st.text_input(
            "Search Query",
            placeholder="Enter movie title or person name...",
            key="search_query",
            help="Type at least 2 characters to search",
        )

        # Options
        col1, col2 = st.columns([1, 4])
        with col1:
            force_refresh = st.checkbox(
                "Force Refresh",
                value=False,
                help="Bypass cache and fetch fresh data from API",
            )

        # Search button
        if st.button("🔎 Search", type="primary", use_container_width=True):
            if not query.strip():
                st.warning("⚠️ Please enter a search query")
                logger.info(f"Empty search query submitted")
                return

            if len(query) < 2:
                st.warning("⚠️ Query must be at least 2 characters")
                return

            # Determine endpoint
            endpoint = "search_title" if search_type == "Title" else "search_name"

            with st.spinner(f"🔎 Searching for '{query}'..."):
                try:
                    logger.info(f"Search initiated: type={search_type}, query={query}")

                    # Fetch from cache manager
                    result, status = cache_manager.get(
                        endpoint=endpoint,
                        query=query,
                        force_refresh=force_refresh,
                    )

                    # Show cache status badge
                    col_status, col_info = st.columns([1, 4])
                    with col_status:
                        render_cache_badge(status)
                    with col_info:
                        if result.get("_cache_meta"):
                            cached_at = result["_cache_meta"].get("cached_at", "N/A")
                            st.caption(f"📅 Cached: {cached_at}")

                    # Handle errors
                    if result.get("error") == "not_found":
                        st.error(f"❌ No results found for '{query}'")
                        logger.info(f"Search returned no results: {query}")
                        return

                    # Display results
                    results = result.get("results", [])
                    if results:
                        st.info(f"Found {len(results)} result(s)")
                        render_search_results(results)
                        logger.info(f"Search successful: {len(results)} results")
                    else:
                        st.info("📭 No results returned from API")

                    # Show any warnings
                    if result.get("errorMessage"):
                        st.warning(f"⚠️ API Message: {result['errorMessage']}")

                except requests.exceptions.RequestException as e:
                    logger.error(
                        f"Network error during search: {e}",
                        exc_info=True,
                        extra={"error_code": "SEARCH_NETWORK_ERROR"},
                    )
                    st.error(f"❌ Network error: {e}")

                except Exception as e:
                    logger.error(
                        f"Search failed: {e}",
                        exc_info=True,
                        extra={"error_code": "SEARCH_FAILED"},
                    )
                    st.error(f"❌ Search failed: {e}")

    except Exception as e:
        logger.error(
            f"Error in search tab: {e}",
            exc_info=True,
            extra={"error_code": "SEARCH_TAB_ERROR"},
        )
        st.error(f"Search tab error: {e}")


def render_detail_tab(cache_manager, registry):
    """Render the detail lookup tab.

    Args:
        cache_manager: CacheManager instance
        registry: EndpointRegistry instance
    """
    try:
        st.subheader("📋 Detail Lookup")

        # Detail type selector
        detail_type = st.radio(
            "Lookup Type",
            [
                "Title Detail",
                "Title Rating",
                "Name Detail",
                "Name Filmography",
            ],
            horizontal=True,
            help="Select what information to retrieve",
        )

        # Map display name to endpoint
        endpoint_map = {
            "Title Detail": "titles_detail",
            "Title Rating": "titles_rating",
            "Name Detail": "names_detail",
            "Name Filmography": "names_filmography",
        }

        endpoint_name = endpoint_map[detail_type]

        # ID input
        resource_id = st.text_input(
            "IMDB ID",
            placeholder="e.g., tt0111161 (for movies) or nm0000093 (for people)",
            key="detail_id",
            help="Enter a valid IMDB ID (starting with tt or nm)",
        )

        # Options
        force_refresh = st.checkbox(
            "Force Refresh",
            value=False,
            key="detail_refresh",
            help="Bypass cache",
        )

        # Lookup button
        if st.button("🔎 Lookup", type="primary", use_container_width=True):
            if not resource_id.strip():
                st.warning("⚠️ Please enter an IMDB ID")
                logger.info("Empty ID submitted")
                return

            with st.spinner(f"📍 Looking up {resource_id}..."):
                try:
                    logger.info(
                        f"Detail lookup initiated: id={resource_id}, type={detail_type}"
                    )

                    # Fetch from cache manager
                    result, status = cache_manager.get(
                        endpoint=endpoint_name,
                        resource_id=resource_id.strip(),
                        force_refresh=force_refresh,
                    )

                    # Show status
                    col_status, col_info = st.columns([1, 4])
                    with col_status:
                        render_cache_badge(status)
                    with col_info:
                        if result.get("_cache_meta"):
                            cached_at = result["_cache_meta"].get("cached_at", "N/A")
                            st.caption(f"📅 Cached: {cached_at}")

                    # Handle not found
                    if result.get("error") == "not_found":
                        st.error(f"❌ Resource '{resource_id}' not found")
                        logger.info(f"Resource not found: {resource_id}")
                        return

                    # View mode selector
                    view_mode = st.radio(
                        "View Mode",
                        ["Pretty", "Table", "Raw JSON"],
                        horizontal=True,
                        key="detail_view",
                    )

                    # Render based on view mode
                    if view_mode == "Pretty":
                        _render_pretty_detail(result, detail_type)
                    elif view_mode == "Table":
                        try:
                            render_table_view(result)
                        except Exception as e:
                            logger.error(f"Error rendering table view: {e}")
                            st.warning(f"Could not render as table: {e}")
                    else:  # Raw JSON
                        render_json_viewer(result)

                    logger.info(f"Detail lookup successful: {resource_id}")

                except requests.exceptions.RequestException as e:
                    logger.error(
                        f"Network error during lookup: {e}",
                        exc_info=True,
                        extra={"error_code": "LOOKUP_NETWORK_ERROR"},
                    )
                    st.error(f"❌ Network error: {e}")

                except Exception as e:
                    logger.error(
                        f"Lookup failed: {e}",
                        exc_info=True,
                        extra={"error_code": "LOOKUP_FAILED"},
                    )
                    st.error(f"❌ Lookup failed: {e}")

    except Exception as e:
        logger.error(
            f"Error in detail tab: {e}",
            exc_info=True,
            extra={"error_code": "DETAIL_TAB_ERROR"},
        )
        st.error(f"Detail tab error: {e}")


def _render_pretty_detail(data: dict, detail_type: str):
    """Render detail data in a pretty format based on type.

    Args:
        data: Response data
        detail_type: Type of detail being displayed
    """
    try:
        if detail_type == "Title Detail":
            col1, col2 = st.columns([1, 2])
            with col1:
                if data.get("image"):
                    try:
                        st.image(data["image"], use_container_width=True)
                    except Exception as e:
                        logger.warning(f"Failed to load image: {e}")
                        st.caption("📸 Image unavailable")

            with col2:
                st.header(data.get("title", "Unknown Title"))
                if data.get("year"):
                    st.caption(f"📅 {data['year']}")
                if data.get("imDbRating"):
                    st.caption(f"⭐ {data['imDbRating']} / 10")
                if data.get("runtimeMins"):
                    st.caption(f"⏱️ {data['runtimeMins']} minutes")
                if data.get("genres"):
                    genres_str = (
                        ", ".join(data["genres"])
                        if isinstance(data["genres"], list)
                        else data["genres"]
                    )
                    st.caption(f"🎭 {genres_str}")
                if data.get("plot"):
                    st.markdown(f"**Plot:** {data['plot']}")
                if data.get("directors"):
                    st.caption(f"🎬 {data['directors']}")
                if data.get("stars"):
                    st.caption(f"🌟 {data['stars']}")

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
                    try:
                        st.image(data["image"], use_container_width=True)
                    except Exception as e:
                        logger.warning(f"Failed to load image: {e}")
                        st.caption("📸 Image unavailable")

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
                    with st.expander(
                        f"{job_title} ({len(titles) if isinstance(titles, list) else 0})",
                        expanded=False,
                    ):
                        if isinstance(titles, list):
                            for t in titles[:20]:
                                st.caption(
                                    f"• {t.get('title', 'Unknown')} ({t.get('year', '')})"
                                )
                            if len(titles) > 20:
                                st.caption(f"... and {len(titles) - 20} more")

    except Exception as e:
        logger.error(
            f"Error rendering pretty detail: {e}",
            exc_info=True,
            extra={"error_code": "PRETTY_RENDER_ERROR"},
        )
        st.error(f"Error rendering: {e}")


def render_cache_tab(cache_manager):
    """Render the cache management tab.

    Args:
        cache_manager: CacheManager instance
    """
    try:
        st.subheader("💾 Cache Management")

        # Display stats
        try:
            stats = cache_manager.get_stats()
            render_stats_panel(stats)
        except Exception as e:
            logger.error(f"Failed to load stats: {e}", exc_info=True)
            st.warning(f"Error loading stats: {e}")

        st.divider()
        st.subheader("🗑️ Invalidate Cache")

        col1, col2 = st.columns(2)
        with col1:
            endpoint_to_clear = st.text_input(
                "Endpoint (leave blank for all)",
                placeholder="e.g., titles_detail",
                key="cache_endpoint",
            )

        with col2:
            resource_to_clear = st.text_input(
                "Resource ID",
                placeholder="e.g., tt0111161",
                key="cache_resource",
            )

        if st.button("🗑️ Invalidate", type="primary", use_container_width=True):
            try:
                if endpoint_to_clear and resource_to_clear:
                    count = cache_manager.db_storage.invalidate_by_resource(
                        endpoint_to_clear,
                        resource_to_clear,
                    )
                    st.success(f"✅ Invalidated {count} entries")
                    logger.info(
                        f"Cache invalidated: endpoint={endpoint_to_clear}, "
                        f"resource={resource_to_clear}, count={count}"
                    )

                elif endpoint_to_clear:
                    count = cache_manager.invalidate_endpoint(endpoint_to_clear)
                    st.success(f"✅ Invalidated {count} entries for endpoint '{endpoint_to_clear}'")
                    logger.info(
                        f"Cache invalidated by endpoint: {endpoint_to_clear}, count={count}"
                    )

                else:
                    st.warning("⚠️ Please specify at least an endpoint")

            except Exception as e:
                logger.error(
                    f"Failed to invalidate cache: {e}",
                    exc_info=True,
                    extra={"error_code": "CACHE_INVALIDATION_FAILED"},
                )
                st.error(f"❌ Error invalidating cache: {e}")

    except Exception as e:
        logger.error(
            f"Error in cache tab: {e}",
            exc_info=True,
            extra={"error_code": "CACHE_TAB_ERROR"},
        )
        st.error(f"Cache tab error: {e}")


def render_main():
    """Main application entry point."""
    try:
        settings, db_manager, api_client, cache_manager, report = initialize_app()

        # Handle initialization failures
        if not cache_manager:
            st.error("⚠️ Application failed to initialize")
            st.subheader("Health Check Report")
            st.json(report)
            logger.error("Application not initialized due to failed health checks")
            st.stop()

        # Render sidebar
        try:
            render_sidebar(cache_manager, EndpointRegistry())
        except Exception as e:
            logger.error(f"Failed to render sidebar: {e}", exc_info=True)

        # Main header
        st.title("🎬 IMDB Cache UI")
        st.markdown(
            "**Free IMDB API client with caching | Rate limit: 1 request/sec**"
        )

        # Three tabs
        tab1, tab2, tab3 = st.tabs(["🔍 Search", "📋 Detail Lookup", "💾 Cache"])

        with tab1:
            render_search_tab(cache_manager, EndpointRegistry())

        with tab2:
            render_detail_tab(cache_manager, EndpointRegistry())

        with tab3:
            render_cache_tab(cache_manager)

        logger.info("Application rendered successfully")

    except Exception as e:
        logger.error(
            f"Critical error in main: {e}",
            exc_info=True,
            extra={"error_code": "MAIN_RENDER_ERROR"},
        )
        st.error(f"Critical error: {e}")


if __name__ == "__main__":
    import requests  # Import at top of main block
    render_main()
