"""The contact-history summary that the manager pastes into Paamonim as a note.
Plain text built from the journal; the tool never writes to Paamonim itself."""
from __future__ import annotations

from app.domain.dates import format_date, today_il
from app.storage import models as m

_KIND = {m.J_OUT: "פנינו", m.J_IN: "התקבלה תשובה", m.J_NOTE: "הערה", m.J_STATUS: "סטטוס"}


def build_summary(cand: m.Candidate) -> str:
    lines = [f"סיכום קשר – מועמד/ת למלווה – נכון ל-{format_date(today_il())}"]
    status = cand.status.name if cand.status else "ללא סטטוס"
    lines.append(f"סטטוס נוכחי: {status}")
    if cand.follow_up_date:
        label = "לפנות שוב ב" if cand.status and cand.status.requires_date else "מעקב ב"
        lines.append(f"{label}-{format_date(cand.follow_up_date)}")
    entries = sorted(cand.journal, key=lambda e: (e.occurred_on, e.id))
    if entries:
        lines.append("")
        lines.append("מהלך הקשר:")
    for e in entries:
        parts = [format_date(e.occurred_on), _KIND.get(e.kind, e.kind)]
        if e.channel:
            parts.append(m.CHANNELS.get(e.channel, e.channel))
        if e.kind == m.J_STATUS and e.status:
            parts.append(e.status.name)
        head = " · ".join(parts)
        lines.append(f"- {head}" + (f": {e.text}" if e.text else ""))
    return "\n".join(lines)
