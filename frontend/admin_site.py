"""Compatibility exports for the dedicated Streamlit admin entry point."""
from frontend.admin.shell import _ADMIN_NAV, _BRIDGE, _SIDEBAR, main, st


__all__ = ["main", "_BRIDGE", "_SIDEBAR", "_ADMIN_NAV", "st"]
