"""Small layout adjustments; colors and widgets follow Streamlit's native theme."""
import streamlit as st


def apply_appearance():
    st.markdown("""<style>
    /* Keep the native theme menu mounted for the sidebar's appearance toggle. */
    [data-testid="stHeader"], [data-testid="stDecoration"] {
        visibility: hidden;
        pointer-events: none;
    }
    .block-container {
        max-width: 96% !important;
        width: 96% !important;
        padding-top: 3.5rem;
        padding-bottom: 2.5rem;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
    }
    h1 {font-size:1.8rem!important;letter-spacing:-.025em;}
    h2 {font-size:1.2rem!important;}
    [data-testid="stVerticalBlock"] {gap:.8rem;}
    [data-testid="stMetricValue"] {font-size:1.6rem;}
    .st-key-workflow_section {margin-bottom:.4rem;}
    .st-key-workflow_content {min-height:65vh;}
    .latest-job-pulse {
        display:flex;align-items:center;gap:.22rem;
    }
    .latest-job-pulse i {
        width:.34rem;height:.34rem;border-radius:50%;background-color:#b66c27;
        animation:latest-job-pulse 1.35s ease-in-out infinite;
    }
    .latest-job-pulse i:nth-child(2) {animation-delay:.17s;}
    .latest-job-pulse i:nth-child(3) {animation-delay:.34s;}
    @keyframes latest-job-pulse {0%,70%,100% {opacity:.38;} 35% {opacity:1;}}
    @media(prefers-reduced-motion:reduce) {.latest-job-pulse i {animation:none;}}
    @media(max-width:640px) {.block-container {padding-left:1rem!important;padding-right:1rem!important;}}

    /* Hide source details expander visually */
    .st-key-hide_source_details_expander,
    .st-key-hide_source_details_expander * {
        display: none !important;
    }

    /* Move Streamlit toast notifications from bottom-right to top-left */
    [data-testid="stToastContainer"] {
        position: fixed !important;
        top: 4rem !important;
        bottom: auto !important;
        right: auto !important;
        left: 1rem !important;
        z-index: 99999999 !important;
        display: flex !important;
        flex-direction: column !important;
        gap: 0.65rem !important;
        pointer-events: auto !important;
    }

    [data-testid="stToast"] {
        min-width: 320px !important;
        max-width: 480px !important;
        padding: 0.85rem 1.15rem !important;
        border-radius: 0.75rem !important;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.25), 0 8px 10px -6px rgba(0, 0, 0, 0.2) !important;
        background-color: var(--secondary-background-color, #ffffff) !important;
        color: var(--text-color, #18332f) !important;
        border: 1px solid var(--border-color, #d5e2df) !important;
        border-left: 6px solid #ef4444 !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        line-height: 1.4 !important;
        animation: toast-slide-down 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
    }

    [data-testid="stToast"] button {
        opacity: 0.8 !important;
        cursor: pointer !important;
        padding: 0.2rem !important;
        border-radius: 4px !important;
        transition: opacity 0.15s ease, background-color 0.15s ease !important;
    }

    [data-testid="stToast"] button:hover {
        opacity: 1 !important;
        background-color: rgba(0, 0, 0, 0.08) !important;
    }

    .st-key-era5_auth_bridge {display:none;}
    .st-key-era5_login_card {max-width:30rem;margin:clamp(1rem,8vh,5rem) auto 0;}
    @media (orientation:portrait) {
        .st-key-era5_login_card {margin-top:0;}
        [data-testid="stToastContainer"] {max-width:calc(100vw - 1rem);}
        body:has(.st-key-ai_chat_shell) [data-testid="stToastContainer"] {top:8rem !important;}
    }

    @keyframes toast-slide-down {
        from {
            transform: translateY(-50px);
            opacity: 0;
        }
        to {
            transform: translateY(0);
            opacity: 1;
        }
    }

    @media (max-width: 640px) {
        [data-testid="stToastContainer"] {
            top: 3.75rem !important;
            right: 0.5rem !important;
            left: 0.5rem !important;
            min-width: auto !important;
        }
        [data-testid="stToast"] {
            min-width: auto !important;
            max-width: 100% !important;
        }
    }

    /* Intuitive visual alert cards */
    .era5-visual-alert {
        display: flex;
        align-items: flex-start;
        gap: 1rem;
        padding: 1.1rem 1.35rem;
        border-radius: 12px;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        border: 1.5px solid transparent;
        animation: toast-slide-down 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .era5-visual-alert.warning {
        background-color: rgba(245, 158, 11, 0.08);
        border-color: #f59e0b;
    }
    .era5-visual-alert.error {
        background-color: rgba(239, 68, 68, 0.08);
        border-color: #ef4444;
    }
    .era5-visual-alert.info {
        background-color: rgba(15, 118, 110, 0.08);
        border-color: #0f766e;
    }
    .era5-visual-alert .alert-icon {
        font-size: 1.8rem;
        line-height: 1;
        flex: none;
    }
    .era5-visual-alert .alert-content {
        flex: 1;
    }
    .era5-visual-alert .alert-title {
        font-weight: 700;
        font-size: 1.05rem;
        margin-bottom: 0.3rem;
        color: var(--text-color, #18332f);
    }
    .era5-visual-alert.warning .alert-title {
        color: #d97706;
    }
    .era5-visual-alert.error .alert-title {
        color: #dc2626;
    }
    .era5-visual-alert .alert-desc {
        font-size: 0.95rem;
        line-height: 1.45;
        opacity: 0.92;
    }
    </style>""", unsafe_allow_html=True)
