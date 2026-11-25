"""Intake session texts: the greeting menu and the end-of-session summary."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Intake, Session

END_LABEL = "⏹ End intake"
RECAP_LABEL = "📋 Recap"


def greeting(first_name: str | None) -> str:
    who = f" {html.escape(first_name)}" if first_name else ""
    return (f"Hi{who}! 👋 What do you want to do?\n\n"
            "1. 📦 <b>Intake</b>: receive stock (photo → check → save to the sheet)\n"
            "2. 🧰 <b>Other tasks</b>\n\n"
            "Tap a button, or type 1 or 2.")


def _local(ts: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(ts).astimezone(tz)


def _rp(v: float) -> str:
    return f"Rp{v:,.0f}"


def summary(session: Session, items: list[Intake], tz: ZoneInfo, tab: str) -> str:
    """End-of-session report: what was received, as written to the sheet."""
    start = _local(session.started_at, tz)
    end = _local(session.ended_at, tz) if session.ended_at else datetime.now(tz)
    minutes = max(1, int((end - start).total_seconds() // 60))
    span = f"{minutes} min" if minutes < 90 else f"{minutes // 60} h {minutes % 60:02d} min"
    same_day = start.date() == end.date()
    when = (f"{start:%a %d %b %Y}, {start:%H:%M}–{end:%H:%M}" if same_day
            else f"{start:%d %b %H:%M} – {end:%d %b %H:%M}")

    saved = [i for i in items if i.status in ("confirmed", "merged") and i.saved_action]
    cancelled = [i for i in items if i.status == "cancelled"]
    pieces = sum(i.saved_qty or 0 for i in saved)
    lines_added = [i for i in saved if i.saved_action == "added"]
    new = [i for i in saved if i.outcome == "new"]
    restock = [i for i in saved if i.outcome == "restock"]
    hbb = sum((i.saved_hbb or 0) * (i.saved_qty or 0) for i in saved if i.saved_hbb is not None)
    hjb = sum((i.saved_hjb or 0) * (i.saved_qty or 0) for i in saved if i.saved_hjb is not None)
    no_cost = [i for i in saved if i.saved_hbb is None]

    out = [f"📋 <b>Intake summary</b> — {html.escape(session.user_name)}",
           f"🕒 {when} ({span})", ""]
    if not saved:
        out.append("Nothing was saved in this session.")
    else:
        counts = f"<b>{len(saved)}</b> item(s), <b>{pieces}</b> pcs"
        if new or restock:
            counts += f" · 🆕 {len(new)} new · 📦 {len(restock)} restock"
        out.append(counts)
        out.append("")
        marks = {"added": "", "replaced": " 🔁 replaced", "merged": " ➕ added to an earlier line"}
        for n, i in enumerate(saved, start=1):
            tag = {"new": " 🆕", "restock": " 📦"}.get(i.outcome or "", "")
            ref = f" · <code>{html.escape(i.saved_sku)}</code>" if i.saved_sku and i.saved_name else ""
            out.append(f"{n}. <code>{html.escape(i.saved_name or i.saved_sku or i.id)}</code>{ref}"
                       f" — <b>{i.saved_qty or 0} pcs</b>{tag}{marks.get(i.saved_action or '', '')}")
        out.append("")
        out.append(f"💰 At cost (HBB): <b>{_rp(hbb)}</b> · at list (HJB): <b>{_rp(hjb)}</b>")
        if no_cost:
            out.append(f"   ({len(no_cost)} SALE item(s) without a cost not included in HBB)")
        if len(lines_added) != len(saved):
            out.append(f"   {len(lines_added)} new sheet line(s); the rest replaced or added to today's lines.")
    if cancelled:
        out.append(f"❌ Cancelled: {len(cancelled)}")
    out += ["", f"🗂 Sheet tab: <b>{html.escape(tab)}</b>. Import after today ends: rows dated today are "
                "skipped by the importer."]
    return "\n".join(out)


def staged_recap(session: Session, items: list[Intake], tz: ZoneInfo) -> str:
    """Everything in this session: staged (confirmed, not yet in the sheet),
    already in the sheet, waiting for a Confirm, and revoked / cancelled."""
    start = _local(session.started_at, tz)
    staged = [i for i in items if i.status == "staged"]
    written = [i for i in items if i.status in ("confirmed", "merged") and i.saved_action]
    pending = [i for i in items if i.status == "pending"]
    gone = [i for i in items if i.status in ("revoked", "cancelled", "replaced")]
    out = [f"📋 <b>Session recap</b> — since {start:%H:%M}"]

    def block(title: str, rows: list[Intake]) -> None:
        what = {"replace": " 🔁 replaces today's line", "addqty": " ➕ adds to today's line",
                "replaced": " 🔁 replaced a line", "merged": " ➕ added to a line"}
        out.append("")
        out.append(title)
        for n, i in enumerate(rows, start=1):
            out.append(f"{n}. <code>{html.escape(i.saved_name or i.id)}</code> · "
                       f"<code>{html.escape(i.saved_sku or '')}</code> — <b>{i.saved_qty or 0} pcs</b>"
                       f"{what.get(i.saved_action or '', '')}")
            out.append(f"    id <code>{html.escape(i.id)}</code>")
        pcs = sum(i.saved_qty or 0 for i in rows)
        hbb = sum((i.saved_hbb or 0) * (i.saved_qty or 0) for i in rows if i.saved_hbb is not None)
        hjb = sum((i.saved_hjb or 0) * (i.saved_qty or 0) for i in rows if i.saved_hjb is not None)
        out.append(f"   <b>{len(rows)}</b> item(s), <b>{pcs}</b> pcs · HBB {_rp(hbb)} · HJB {_rp(hjb)}")

    if staged:
        block(f"📝 <b>Staged — NOT in the sheet yet</b> (written when you tap {END_LABEL})", staged)
    if written:
        block("💾 <b>Already in the sheet</b>", written)
    if not staged and not written:
        out += ["", "Nothing confirmed in this session yet."]
    if pending:
        out += ["", "⏳ Not confirmed yet ({}): {}".format(
            len(pending), ", ".join(f"<code>{html.escape(i.id)}</code>" for i in pending))]
    if gone:
        out += ["", f"🗑 Revoked / cancelled: {len(gone)} (not counted above)"]
    out += ["", f"Undo one: <code>revoke &lt;id&gt;</code>."]
    return "\n".join(out)
