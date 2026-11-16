"""Small layout adjustments; colors and widgets follow Streamlit's native theme."""
import streamlit as st


def apply_appearance():
    st.markdown("""<style>
    .block-container {max-width:1100px;padding-top:4rem;padding-bottom:2rem;}
    h1 {font-size:1.8rem!important;letter-spacing:-.025em;}
    h2 {font-size:1.2rem!important;}
    [data-testid="stVerticalBlock"] {gap:.8rem;}
    [data-testid="stMetricValue"] {font-size:1.6rem;}
    .st-key-workflow_section {margin-bottom:.4rem;}
    @media(max-width:640px) {.block-container {padding-left:1rem;padding-right:1rem;}}
    </style>""", unsafe_allow_html=True)
