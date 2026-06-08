"""
Sidebar component for the Somalia Economic Intelligence Platform dashboard.

Renders:
  - Platform title + tagline
  - Auto-refresh selector (returns chosen interval in seconds, or 0 for Off)
  - Database status indicator
  - Pipeline health (core vs optional sources)
  - Last refresh timestamp
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dashboard.queries import (
    get_database_status,
    compute_pipeline_health,
)


def render_sidebar() -> int:
    """
    Render the sidebar and return the selected auto-refresh interval in seconds.
    Returns 0 when auto-refresh is off.
    """
    with st.sidebar:
        st.markdown("## 🌍 Somalia EIP")
        st.caption("Real-time public data monitoring and analytics")
        st.divider()

        # ------------------------------------------------------------------ #
        # Auto-refresh selector
        # ------------------------------------------------------------------ #
        st.subheader("Auto Refresh")
        refresh_options = {
            "Off": 0,
            "30 seconds": 30,
            "1 minute": 60,
            "5 minutes": 300,
            "10 minutes": 600,
        }
        selected_label = st.selectbox(
            "Refresh interval",
            options=list(refresh_options.keys()),
            index=0,
            label_visibility="collapsed",
        )
        refresh_seconds: int = refresh_options[selected_label]
        st.divider()

        # ------------------------------------------------------------------ #
        # Database status
        # ------------------------------------------------------------------ #
        st.subheader("Database")
        db_status = get_database_status()
        if db_status.get("connected"):
            st.success("Connected")
            st.caption(f"{db_status.get('total_records', 0):,} records")
        else:
            st.error("Disconnected")
            st.caption(db_status.get("error", "Unknown error"))
        st.divider()

        # ------------------------------------------------------------------ #
        # Pipeline health — core / optional split
        # ------------------------------------------------------------------ #
        st.subheader("Pipeline Health")
        health = compute_pipeline_health()
        core_status = health.get("core_status")
        opt_unavail = int(health.get("optional_unavailable", 0))

        # DEBUG: show health in sidebar for troubleshooting

        if core_status == "Healthy":
            st.success("Core Sources: Healthy")
        else:
            st.warning("Core Sources: Issue detected")
            for label, detail in health.get("core_detail", []):
                colour = "🟢" if str(detail).startswith("✓") else "🔴"
                st.caption(f"{colour} {label}: {detail}")

        if opt_unavail > 0:
            st.info(f"Optional Sources: {opt_unavail} unavailable")
        else:
            st.success("Optional Sources: OK")

        st.divider()

        # ------------------------------------------------------------------ #
        # Session info
        # ------------------------------------------------------------------ #
        st.subheader("Session")
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        st.caption(f"Refreshed: {now_str}")

    return refresh_seconds
