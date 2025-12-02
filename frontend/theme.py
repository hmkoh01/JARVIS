#!/usr/bin/env python3
"""
JARVIS UI Theme Configuration
모든 프론트엔드 파일에서 공유하는 색상 팔레트 및 스타일 정의

디자인 원칙:
- 파란색 계열(Indigo)을 핵심 브랜드 색상으로 사용
- 버튼은 명확한 대비를 위해 "outlined" 스타일을 기본으로 사용
- 텍스트는 항상 배경과 충분한 대비를 유지
"""

# =============================================================================
# 전역 색상 팔레트
# =============================================================================
COLORS = {
    # Surface colors (배경)
    "surface": "#FFFFFF",           # 기본 흰색 배경
    "surface_alt": "#F9FAFB",       # 연한 회색 배경 (카드, 입력 필드)
    "panel_bg": "#F3F4F6",          # 패널/섹션 배경
    
    # Brand colors (브랜드 색상 - Indigo 계열)
    "primary": "#4F46E5",           # 메인 브랜드 색상 (헤더, 강조)
    "primary_dark": "#4338CA",      # 호버/활성 상태
    "primary_soft": "#EEF2FF",      # 연한 배경 (선택된 항목)
    
    # Text colors (텍스트 - 명확한 대비 보장)
    "text_primary": "#111827",      # 기본 텍스트 (거의 검정)
    "text_secondary": "#374151",    # 보조 텍스트 (진한 회색)
    "text_muted": "#6B7280",        # 비활성/힌트 텍스트
    "text_inverse": "#FFFFFF",      # 어두운 배경 위 텍스트 (흰색)
    
    # Border & Divider
    "border": "#D1D5DB",            # 테두리 (약간 진하게 조정)
    "border_light": "#E5E7EB",      # 연한 테두리
    
    # Semantic colors (상태별 색상)
    "info_bg": "#DBEAFE",
    "info_text": "#1E40AF",
    "success_bg": "#D1FAE5",
    "success_text": "#065F46",
    "danger_bg": "#FEE2E2",
    "danger_text": "#991B1B",
    "warning_bg": "#FEF3C7",
    "warning_text": "#92400E",
    
    # Button states
    "button_disabled_bg": "#E5E7EB",
    "button_disabled_text": "#9CA3AF",
    
    # Dashboard specific colors
    "dashboard_header": "#1E293B",
    "dashboard_card": "#FFFFFF",
    "dashboard_card_border": "#E2E8F0",
    "chart_primary": "#6366F1",
    "chart_secondary": "#8B5CF6",
    "chart_tertiary": "#EC4899",
    "chart_success": "#10B981",
    "chart_warning": "#F59E0B",
    "note_bg": "#FFFBEB",
    "note_border": "#FCD34D",
}

# =============================================================================
# 상태 배지 스타일
# =============================================================================
STATUS_BADGE_STYLES = {
    "pending": {"bg": COLORS["warning_bg"], "fg": COLORS["warning_text"]},
    "accepted": {"bg": COLORS["success_bg"], "fg": COLORS["success_text"]},
    "rejected": {"bg": "#E5E7EB", "fg": COLORS["text_secondary"]},
    "shown": {"bg": "#DBEAFE", "fg": "#1D4ED8"},
    "completed": {"bg": COLORS["success_bg"], "fg": COLORS["success_text"]},
    "default": {"bg": COLORS["border_light"], "fg": COLORS["text_secondary"]},
}

# =============================================================================
# 버튼 스타일 variants
# 
# "outlined" - 기본 CTA 버튼 (테두리 + 진한 텍스트, 명확한 대비)
# "ghost"    - 보조 버튼 (최소한의 스타일)
# "danger"   - 위험/삭제 액션
# "primary"  - outlined의 별칭 (하위 호환성)
# "secondary"- outlined의 별칭 (하위 호환성)
# =============================================================================
BUTTON_STYLES = {
    # 기본 CTA 버튼: 연한 배경 + 테두리 + 진한 텍스트 (명확한 대비)
    "outlined": {
        "bg": COLORS["surface_alt"],
        "fg": COLORS["text_primary"],
        "active_bg": COLORS["border"],
        "active_fg": COLORS["text_primary"],
        "border_color": COLORS["border"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    # 보조 버튼: 최소한의 스타일 (배경 거의 없음)
    "ghost": {
        "bg": COLORS["surface"],
        "fg": COLORS["text_secondary"],
        "active_bg": COLORS["surface_alt"],
        "active_fg": COLORS["text_primary"],
        "border_color": COLORS["border_light"],
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    # 위험/삭제 액션
    "danger": {
        "bg": COLORS["danger_bg"],
        "fg": COLORS["danger_text"],
        "active_bg": "#FECACA",
        "active_fg": COLORS["danger_text"],
        "border_color": "#FECACA",
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
    # 성공/확인 액션
    "success": {
        "bg": COLORS["success_bg"],
        "fg": COLORS["success_text"],
        "active_bg": "#A7F3D0",
        "active_fg": COLORS["success_text"],
        "border_color": "#A7F3D0",
        "disabled_bg": COLORS["button_disabled_bg"],
        "disabled_fg": COLORS["button_disabled_text"],
    },
}

# 하위 호환성: primary와 secondary는 outlined의 별칭
BUTTON_STYLES["primary"] = BUTTON_STYLES["outlined"]
BUTTON_STYLES["secondary"] = BUTTON_STYLES["outlined"]


def style_button(button, variant="outlined", disabled=False):
    """버튼에 일관된 테마 스타일을 적용합니다.
    
    Args:
        button: tk.Button 위젯
        variant: "outlined" (기본), "ghost", "danger", "success" 중 하나
                 "primary", "secondary"도 하위 호환성을 위해 지원 (outlined과 동일)
        disabled: 비활성화 여부
    """
    # 기본값은 outlined (명확한 대비를 가진 CTA 버튼)
    style = BUTTON_STYLES.get(variant, BUTTON_STYLES["outlined"]).copy()
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
            "bg": style.get("bg", COLORS["surface_alt"]),
            "fg": style.get("fg", COLORS["text_primary"]),
            "activebackground": style.get("active_bg", COLORS["border"]),
            "activeforeground": style.get("active_fg", COLORS["text_primary"]),
            "state": 'normal',
            "cursor": 'hand2',
        })
    
    button.configure(**config)

