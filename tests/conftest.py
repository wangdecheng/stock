"""tests/conftest.py — shared fixtures for UI tests.

The autouse ``_reset_streamlit_caches`` fixture clears Streamlit's
``@st.cache_data`` store between tests so AppTest instances don't replay
a previous test's cached reads (Streamlit's in-memory cache survives the
test function return).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_streamlit_caches():
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass
    yield
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass