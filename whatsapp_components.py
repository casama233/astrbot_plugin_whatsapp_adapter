"""WhatsApp 平台專用訊息元件，供插件在 MessageChain 中使用。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WhatsAppButton:
    """單個互動按鈕（最多 3 個）。"""

    text: str
    id: str = ""
    type: str = "whatsapp_button"


@dataclass
class WhatsAppButtons:
    """WhatsApp 按鈕訊息（quick-reply buttons）。"""

    body: str
    buttons: list[WhatsAppButton] = field(default_factory=list)
    footer: str = ""
    type: str = "whatsapp_buttons"


@dataclass
class WhatsAppListRow:
    """清單選項列。"""

    title: str
    description: str = ""
    id: str = ""
    type: str = "whatsapp_list_row"


@dataclass
class WhatsAppListSection:
    """清單區塊。"""

    title: str
    rows: list[WhatsAppListRow] = field(default_factory=list)
    type: str = "whatsapp_list_section"


@dataclass
class WhatsAppList:
    """WhatsApp 清單選擇訊息。"""

    title: str
    description: str = ""
    button_text: str = "選項"
    sections: list[WhatsAppListSection] = field(default_factory=list)
    footer: str = ""
    type: str = "whatsapp_list"


@dataclass
class WhatsAppPoll:
    """WhatsApp 投票訊息（出站）。"""

    name: str
    options: list[str] = field(default_factory=list)
    selectable_count: int = 0
    type: str = "whatsapp_poll"


@dataclass
class WhatsAppEdit:
    """編輯已發送的文字訊息（出站）。"""

    message_id: str
    text: str
    participant: str | None = None
    type: str = "whatsapp_edit"
