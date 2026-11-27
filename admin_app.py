"""Dedicated Streamlit entrypoint for the ERA5 administration site."""
import streamlit as st

from frontend.admin_site import main


if __name__ == "__main__":
    st.set_page_config(page_title="Weather Hub Admin", page_icon="🛡️", layout="wide")
    main()
