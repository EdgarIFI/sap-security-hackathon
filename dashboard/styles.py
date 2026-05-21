"""
dashboard/styles.py
===================
CSS injection and branding helpers for the SAP SOC Dashboard.
Shared between app_dashboard.py (desktop) and app_mobile.py (mobile).

Usage in app_dashboard.py:
    from styles import inject_css, render_logo_header, severity_badge
    inject_css(threat_level)      # call after getting kpis in main()
    render_logo_header(subtitle)  # replaces render_header() title area

Usage in app_mobile.py:
    from styles import inject_mobile_css, render_logo_header, severity_badge
    inject_mobile_css()

Logo:
    Place your PNG at dashboard/assets/logo.png
    Falls back to shield emoji if file not found.

Owner: Design
"""

import os
import streamlit as st

# ── Color tokens ──────────────────────────────────────────────────────────────
_C = {
    "bg":       "#08090F",
    "card":     "#0D0F1A",
    "card2":    "#111420",
    "border":   "#1C2035",
    "border2":  "#252A40",
    "blue":     "#0066CC",
    "blue2":    "#1A7FE8",
    "blue_a":   "rgba(0,102,204,0.12)",
    "red":      "#FF3B3B",
    "red_a":    "rgba(255,59,59,0.12)",
    "amber":    "#FFAA00",
    "amber_a":  "rgba(255,170,0,0.10)",
    "green":    "#00C853",
    "green_a":  "rgba(0,200,83,0.10)",
    "text":     "#E8ECF4",
    "text2":    "#8892A4",
    "text3":    "#4A5266",
    "mono":     "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
    "sans":     "'IBM Plex Sans', 'DM Sans', 'system-ui', sans-serif",
}

# ── Base CSS (desktop) ────────────────────────────────────────────────────────
_BASE = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [data-testid="stAppViewContainer"] {{
    background: {bg} !important;
    font-family: {sans} !important;
    color: {text} !important;
}}
[data-testid="stMain"] {{ background: {bg} !important; padding-top: 1.2rem; }}
[data-testid="stDecoration"], #MainMenu, footer, header {{ display:none !important; }}
.block-container {{ padding-top: 1rem !important; max-width: 100% !important; }}

/* ── Scrollbar ── */
::-webkit-scrollbar {{ width:4px; height:4px; }}
::-webkit-scrollbar-track {{ background:{bg}; }}
::-webkit-scrollbar-thumb {{ background:{border2}; border-radius:2px; }}
::-webkit-scrollbar-thumb:hover {{ background:{blue}; }}

/* ── KPI Cards ── */
[data-testid="metric-container"] {{
    background: {card} !important;
    border: 1px solid {border} !important;
    border-radius: 10px !important;
    padding: 18px 20px 14px !important;
    position: relative;
    overflow: hidden;
    transition: border-color .2s, box-shadow .2s;
}}
[data-testid="metric-container"]::before {{
    content:''; position:absolute; top:0; left:0; right:0; height:2px;
    background: {blue}; opacity:.7;
}}
[data-testid="metric-container"]:hover {{
    border-color: {border2} !important;
    box-shadow: 0 4px 20px {blue_a} !important;
}}
[data-testid="stMetricLabel"] {{
    font-family: {mono} !important; font-size:.68rem !important;
    letter-spacing:.08em !important; text-transform:uppercase !important;
    color: {text2} !important;
}}
[data-testid="stMetricValue"] {{
    font-size:1.55rem !important; font-weight:600 !important;
    color: {text} !important; letter-spacing:-.02em !important;
}}
[data-testid="stMetricDelta"] {{ font-family:{mono} !important; font-size:.75rem !important; }}

/* ── Tabs ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{
    background: {card} !important; border-radius:10px !important;
    padding:4px !important; border:1px solid {border} !important; gap:4px;
}}
[data-testid="stTabs"] [data-baseweb="tab"] {{
    background:transparent !important; border-radius:7px !important;
    color:{text2} !important; font-family:{sans} !important;
    font-weight:500 !important; font-size:.88rem !important;
    padding:8px 20px !important; transition:all .2s !important; border:none !important;
}}
[data-testid="stTabs"] [aria-selected="true"] {{
    background:{blue} !important; color:#FFFFFF !important;
    box-shadow: 0 2px 12px {blue_a} !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ display:none !important; }}

/* ── Buttons ── */
[data-testid="stButton"] > button {{
    background:transparent !important; border:1px solid {border2} !important;
    color:{text} !important; border-radius:7px !important;
    font-family:{sans} !important; font-size:.82rem !important;
    font-weight:500 !important; transition:all .18s !important;
    padding:6px 14px !important;
}}
[data-testid="stButton"] > button:hover {{
    background:{blue_a} !important; border-color:{blue} !important;
    box-shadow: 0 0 12px {blue_a} !important;
}}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {{
    background:{card} !important; border:1px solid {border} !important;
    border-radius:10px !important; margin-bottom:8px !important;
    padding:14px 16px !important;
}}
[data-testid="stChatInput"] {{
    background:{card} !important; border:1px solid {border2} !important;
    border-radius:10px !important;
}}
[data-testid="stChatInput"] textarea {{
    background:transparent !important; color:{text} !important;
    font-family:{sans} !important; font-size:.9rem !important;
}}

/* ── Typography ── */
h3, h4, [data-testid="stSubheader"] {{
    font-family:{sans} !important; font-weight:600 !important;
    color:{text} !important; letter-spacing:.01em !important;
}}
[data-testid="stCaptionContainer"] {{
    color:{text2} !important; font-family:{mono} !important; font-size:.72rem !important;
}}
hr {{ border-color:{border} !important; margin:10px 0 !important; }}

/* ── Code blocks (IP addresses) ── */
code {{
    background:rgba(26,127,232,.08) !important; border:1px solid {border2} !important;
    color:{blue2} !important; border-radius:4px !important;
    padding:1px 6px !important; font-family:{mono} !important; font-size:.82rem !important;
}}

/* ── Multiselect tags ── */
[data-baseweb="select"] [data-baseweb="tag"] {{
    background:{blue_a} !important; border:1px solid {blue} !important;
    border-radius:4px !important; color:{text} !important; font-size:.75rem !important;
}}

/* ── Spinner ── */
[data-testid="stSpinner"] {{ color:{blue} !important; }}

/* ── Alert / info boxes ── */
[data-testid="stAlert"] {{
    background:{card} !important; border-radius:8px !important;
    border:1px solid {border} !important;
}}

/* ── Live pulse animation ── */
@keyframes soc-pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.25; }} }}
.soc-live-dot {{
    display:inline-block; width:7px; height:7px; border-radius:50%;
    background:{green}; animation:soc-pulse 2s infinite;
    margin-right:6px; vertical-align:middle;
}}
"""

# ── Mobile overrides ──────────────────────────────────────────────────────────
_MOBILE = """
[data-testid="column"] {{
    width:100% !important; flex:1 1 100% !important; min-width:100% !important;
}}
[data-testid="stButton"] > button {{
    min-height:44px !important; font-size:.9rem !important; padding:10px 16px !important;
}}
[data-testid="stMetricValue"] {{ font-size:1.9rem !important; }}
[data-testid="stHorizontalBlock"] {{ flex-wrap:wrap !important; }}
.js-plotly-plot {{ max-height:260px !important; }}
[data-testid="stTabs"] [data-baseweb="tab"] {{ padding:8px 10px !important; font-size:.8rem !important; }}
[data-testid="stChatInput"] textarea {{ font-size:1rem !important; min-height:48px !important; }}
"""

# ── Threat level card glow ────────────────────────────────────────────────────
_THREAT_GLOW = {
    "CRITICAL": ("#FF3B3B", "rgba(255,59,59,0.08)", "rgba(255,59,59,0.25)"),
    "ELEVATED": ("#FFAA00", "rgba(255,170,0,0.08)",  "rgba(255,170,0,0.2)"),
    "NORMAL":   ("#0066CC", "rgba(0,102,204,0.08)",  "rgba(0,102,204,0.15)"),
}


# ── Public API ────────────────────────────────────────────────────────────────

def inject_css(threat_level: str = "NORMAL") -> None:
    """
    Injects full desktop CSS. Call once at the top of main() after getting kpis.

    Args:
        threat_level: 'NORMAL' | 'ELEVATED' | 'CRITICAL'
                      Adds colored top border + glow to the KPI 1 card.
    """
    color, bg_glow, border_glow = _THREAT_GLOW.get(
        threat_level, _THREAT_GLOW["NORMAL"]
    )

    threat_css = f"""
    /* Threat level — KPI 1 card accent */
    div[data-testid="column"]:first-child [data-testid="metric-container"]::before {{
        background: {color} !important; opacity: 1 !important;
    }}
    div[data-testid="column"]:first-child [data-testid="metric-container"] {{
        background: {bg_glow} !important;
        border-color: {border_glow} !important;
    }}
    """

    css = _BASE.format(**_C) + threat_css
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def inject_mobile_css() -> None:
    """Injects desktop + mobile override CSS. Call at top of app_mobile.py main()."""
    css = _BASE.format(**_C) + _MOBILE.format(**_C)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_logo_header(subtitle: str = "") -> None:
    """
    Renders branded header: logo + title + live indicator + subtitle.
    Replaces the st.markdown("## 🛡️ ...") block in render_header().

    Logo: place PNG at dashboard/assets/logo.png
    Falls back to shield emoji if file not found.

    Args:
        subtitle: caption line (last refresh, pipeline status, etc.)
    """
    logo_path = os.path.join(os.path.dirname(__file__), "assets", "logo.png")

    col_logo, col_title = st.columns([1, 10])

    with col_logo:
        if os.path.exists(logo_path):
            st.image(logo_path, width=48)
        else:
            st.markdown(
                "<div style='font-size:2rem;padding-top:6px;'>🛡️</div>",
                unsafe_allow_html=True,
            )

    with col_title:
        st.markdown(
            f"""
            <div style="padding-top:4px;">
                <p style="margin:0;font-size:1.35rem;font-weight:600;
                          letter-spacing:-.02em;color:{_C['text']};
                          font-family:{_C['sans']};">
                    SAP AI Security Operations Center
                </p>
                <p style="margin:0;font-size:.72rem;color:{_C['text2']};
                          font-family:{_C['mono']};letter-spacing:.03em;">
                    <span class="soc-live-dot"></span>LIVE &nbsp;·&nbsp; {subtitle}
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def severity_badge(severity: str) -> str:
    """
    Returns an HTML severity badge string.
    Usage: st.markdown(severity_badge("high"), unsafe_allow_html=True)
    """
    cfg = {
        "high":   (_C["red_a"],   _C["red"],   "rgba(255,59,59,.3)"),
        "medium": (_C["amber_a"], _C["amber"], "rgba(255,170,0,.3)"),
        "low":    (_C["green_a"], _C["green"], "rgba(0,200,83,.2)"),
    }
    bg, color, border = cfg.get(severity, ("transparent", _C["text2"], _C["border"]))
    return (
        f'<span style="font-family:monospace;font-size:.7rem;font-weight:700;'
        f'padding:2px 8px;border-radius:4px;background:{bg};'
        f'color:{color};border:1px solid {border};">{severity.upper()}</span>'
    )