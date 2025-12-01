#!/usr/bin/env python3
"""
JARVIS UI Theme Configuration
모든 프론트엔드 파일에서 공유하는 색상 팔레트 및 스타일 정의
"""

# 전역 색상 팔레트
COLORS = {
    # Surface colors (배경)
    "surface": "#FFFFFF",
    "surface_alt": "#F9FAFB",
    "panel_bg": "#F3F4F6",
    
    # Primary colors (메인 브랜드 색상)
    "primary": "#4F46E5",
    "primary_dark": "#4338CA",
    "primary_soft": "#EEF2FF",
    
    # Text colors (텍스트)
    "text_primary": "#111827",
    "text_secondary": "#374151",
    "text_muted": "#6B7280",
    "text_inverse": "#EEEEEE",
    
    # Border & Divider
    "border": "#E5E7EB",
    
    # Semantic colors (상태별 색상)
    "info_bg": "#E0F2FE",
    "info_text": "#0F172A",
    "success_bg": "#ECFDF5",
    "success_text": "#166534",
    "danger_bg": "#FEE2E2",
    "danger_text": "#B91C1C",
    "warning_bg": "#FEF3C7",
    "warning_text": "#92400E",
    
    # Button states
    "button_disabled_bg": "#E5E7EB",
    "button_disabled_text": "#9CA3AF",
}

# 상태 배지 스타일
STATUS_BADGE_STYLES = {
    "pending": {"bg": COLORS["warning_bg"], "fg": COLORS["warning_text"]},
    "accepted": {"bg": COLORS["success_bg"], "fg": COLORS["success_text"]},
    "rejected": {"bg": "#E5E7EB", "fg": COLORS["text_secondary"]},
    "shown": {"bg": "#DBEAFE", "fg": "#1D4ED8"},
    "completed": {"bg": COLORS["success_bg"], "fg": COLORS["success_text"]},
    "default": {"bg": COLORS["border"], "fg": COLORS["text_secondary"]},
}

# 버튼 스타일 variants
BUTTON_STYLES = {
    "primary": {
        "bg": COLORS["primary"],
        "fg": COLORS["text_inverse"],
        "active_bg": COLORS["primary_dark"],
        "active_fg": COLORS["text_inverse"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    "secondary": {
        "bg": COLORS["surface_alt"],
        "fg": COLORS["text_primary"],
        "active_bg": COLORS["border"],
        "active_fg": COLORS["text_primary"],
        "border_color": COLORS["border"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    "ghost": {
        "bg": COLORS["surface"],
        "fg": COLORS["text_secondary"],
        "active_bg": COLORS["surface_alt"],
        "active_fg": COLORS["text_primary"],
        "border_color": COLORS["border"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    "danger": {
        "bg": COLORS["danger_bg"],
        "fg": COLORS["danger_text"],
        "active_bg": "#FCA5A5",
        "active_fg": COLORS["danger_text"],
        "border_color": COLORS["danger_bg"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
}


def style_button(button, variant="primary", disabled=False):
    """버튼에 일관된 테마 스타일을 적용합니다.
    
    Args:
        button: tk.Button 위젯
        variant: "primary", "secondary", "ghost", "danger" 중 하나
        disabled: 비활성화 여부
    """
    import tkinter as tk
    
    style = BUTTON_STYLES.get(variant, BUTTON_STYLES["primary"]).copy()
    config = {
        "relief": 'flat',
        "bd": 0,
    }
    
    border_color = style.get("border_color")
    border_width = style.get("border_width", 1 if border_color else 0)
    if border_color:
        config.update({
            "highlightbackground": border_color,
            "highlightcolor": border_color,
            "highlightthickness": border_width,
        })
    else:
        config["highlightthickness"] = 0
    
    if disabled:
        config.update({
            "bg": style.get("disabled_bg", COLORS["button_disabled_bg"]),
            "fg": style.get("disabled_fg", COLORS["button_disabled_text"]),
            "state": 'disabled',
            "cursor": 'arrow',
        })
    else:
        config.update({
            "bg": style.get("bg", COLORS["primary"]),
            "fg": style.get("fg", COLORS["text_inverse"]),
            "activebackground": style.get("active_bg", style.get("bg", COLORS["primary"])),
            "activeforeground": style.get("active_fg", style.get("fg", COLORS["text_inverse"])),
            "state": 'normal',
            "cursor": 'hand2',
        })
    
    button.configure(**config)

