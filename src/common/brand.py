"""Lapse look for server-rendered HTML: emails (table layout, inline styles) and pages.

Emails can't rely on <style> or CSS variables (Gmail and Outlook strip much of
it), so they use tables and inline styles. Pages are ordinary HTML that match
the web app, including dark mode.
"""

from __future__ import annotations

import html
from datetime import date

from common.dates import days_until, parse_expiry

ACCENT = "#0f766e"
INK = "#16201e"
MUTED = "#5f6b68"
BG = "#f6f5f1"
BORDER = "#e3e1da"
TONES = {"ok": "#15803d", "warn": "#b45309", "danger": "#c2410c", "accent": ACCENT, "gray": MUTED}
TYPE_LABELS = {
    "insurance": "Insurance",
    "warranty": "Warranty",
    "passport": "Passport / ID",
    "subscription": "Subscription",
    "other": "Document",
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

e = html.escape


def nice_date(value: str) -> str:
    """'2027-01-21' -> '21 Jan 2027' (falls back to the raw text)."""
    d = parse_expiry(value)
    return f"{d.day} {d:%b %Y}" if d else value


def countdown(value: str, today: date | None = None) -> tuple[str, str, str]:
    """(big text, small text, tone) for a date: ('12', 'days left', 'warn'), ('Expired', '10 days ago', 'danger')."""
    days = days_until(value, today)
    if days is None:
        return ("–", "no date", "gray")
    if days < 0:
        n = -days
        return ("Expired", f"{n} day{'s' if n != 1 else ''} ago", "danger")
    if days == 0:
        return ("Today", "expires", "danger")
    tone = "danger" if days <= 7 else "warn" if days <= 30 else "accent"
    if days < 60:
        return (str(days), "day left" if days == 1 else "days left", tone)
    if days < 730:
        months = round(days / 30.44)
        return (str(months), "months left", tone)
    return (f"{days / 365.25:.1f}".rstrip("0").rstrip("."), "years left", tone)


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def email_button(url: str, label: str, primary: bool = True) -> str:
    bg, fg, border = (ACCENT, "#ffffff", ACCENT) if primary else ("#ffffff", INK, "#d2cfc6")
    return (
        f'<a href="{e(url, quote=True)}" style="display:inline-block;background:{bg};color:{fg};'
        f"border:1px solid {border};border-radius:10px;padding:13px 22px;font-family:{FONT};font-size:15px;"
        f'font-weight:600;text-decoration:none;margin:0 8px 10px 0;">{e(label)}</a>'
    )


def email_item_card(name: str, provider: str, kind: str, expiry: str) -> str:
    big, small, tone = countdown(expiry)
    color = TONES[tone]
    sub = " · ".join(x for x in (provider, TYPE_LABELS.get(kind, "Document")) if x)
    when = f"Expired {nice_date(expiry)}" if big == "Expired" else f"Expires {nice_date(expiry)}"
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-left:4px solid {color};border-radius:12px;background:#ffffff;">
  <tr>
    <td style="padding:18px 20px;font-family:{FONT};">
      <div style="font-size:17px;font-weight:700;color:{INK};">{e(name)}</div>
      <div style="font-size:13px;color:{MUTED};margin-top:3px;">{e(sub)}</div>
      <div style="font-size:13px;color:{MUTED};margin-top:2px;">{e(when)}</div>
    </td>
    <td align="right" style="padding:18px 20px;font-family:{FONT};white-space:nowrap;">
      <div style="font-size:30px;line-height:1;font-weight:800;color:{color};letter-spacing:-0.5px;">{e(big)}</div>
      <div style="font-size:12px;color:{MUTED};margin-top:4px;">{e(small)}</div>
    </td>
  </tr>
</table>"""


def email_html(*, preheader: str, heading: str, intro: str, content: str = "", footer: str = "") -> str:
    """Full email document. ``intro``, ``content`` and ``footer`` are trusted HTML built by the caller."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{e(heading)}</title>
</head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG};">{e(preheader)}&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
  <tr><td align="center" style="padding:28px 14px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
      <tr><td style="padding:0 4px 18px;font-family:{FONT};">
        <table role="presentation" cellpadding="0" cellspacing="0"><tr>
          <td style="background:{ACCENT};border-radius:8px;width:28px;height:28px;text-align:center;vertical-align:middle;color:#ffffff;font-size:16px;font-weight:800;">&#9719;</td>
          <td style="padding-left:9px;font-size:18px;font-weight:800;color:{INK};letter-spacing:-0.3px;">Lapse</td>
        </tr></table>
      </td></tr>
      <tr><td style="background:#ffffff;border:1px solid {BORDER};border-radius:16px;padding:28px 26px;font-family:{FONT};">
        <h1 style="margin:0 0 10px;font-size:22px;line-height:1.25;color:{INK};letter-spacing:-0.3px;">{e(heading)}</h1>
        <div style="font-size:15px;line-height:1.55;color:{MUTED};">{intro}</div>
        {content}
      </td></tr>
      <tr><td style="padding:18px 8px 0;font-family:{FONT};font-size:12px;line-height:1.5;color:#8b9592;">{footer}</td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Pages (confirmation pages behind the email buttons)
# ---------------------------------------------------------------------------

PAGE_CSS = """
:root{--bg:#f6f5f1;--bg-soft:#efede7;--card:#fff;--text:#16201e;--muted:#5f6b68;--faint:#8b9592;--border:#e3e1da;--border-strong:#d2cfc6;
--accent:#0f766e;--accent-hover:#0b5f58;--accent-soft:#e3f2ef;--accent-ink:#fff;--ok:#15803d;--ok-soft:#e4f4e8;--warn:#b45309;--warn-soft:#fdf0dc;
--danger:#c2410c;--danger-soft:#fde8de;--gray:#5f6b68;--gray-soft:#eeede8}
@media (prefers-color-scheme:dark){:root{--bg:#0f1413;--bg-soft:#161c1b;--card:#18201e;--text:#e8eeec;--muted:#9fadaa;--faint:#74827f;--border:#263130;
--border-strong:#34413f;--accent:#2dd4bf;--accent-hover:#5eead4;--accent-soft:#123a36;--accent-ink:#062320;--ok:#4ade80;--ok-soft:#12301d;
--warn:#fbbf24;--warn-soft:#36290c;--danger:#fb923c;--danger-soft:#3a1d10;--gray:#9fadaa;--gray-soft:#222b29}}
*{box-sizing:border-box}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:15px;line-height:1.5;
background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}
.wrap{max-width:480px;margin:0 auto;padding:22px 16px 48px}
.brand{display:flex;align-items:center;gap:9px;font-weight:780;font-size:18px;color:var(--text);text-decoration:none;margin-bottom:22px}
.mark{width:28px;height:28px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center}
.mark svg{width:18px;height:18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:26px 24px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -12px rgba(0,0,0,.14)}
.badge{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;margin-bottom:14px;background:var(--tone-soft);color:var(--tone)}
.badge svg{width:26px;height:26px}
h1{font-size:22px;line-height:1.25;letter-spacing:-.02em;margin:0 0 6px}
p{margin:0;color:var(--muted)}
.item{display:flex;justify-content:space-between;gap:12px;align-items:center;border:1px solid var(--border);border-left:4px solid var(--tone);
border-radius:12px;padding:14px 16px;margin:18px 0 4px;background:var(--bg)}
.item b{display:block;font-size:16px}.item small{color:var(--muted);font-size:13px}
.count{text-align:right;line-height:1.05;white-space:nowrap}.count b{font-size:24px;color:var(--tone);letter-spacing:-.02em}.count small{display:block}
label{display:block;font-weight:620;font-size:14px;margin:20px 0 7px}
input[type=date]{width:100%;font:inherit;font-size:16px;color:var(--text);background:var(--card);border:1px solid var(--border-strong);border-radius:10px;padding:11px 12px}
input[type=date]:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
.quick{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.quick button{font:inherit;font-size:12.5px;font-weight:600;padding:5px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--muted);cursor:pointer}
.btn{display:flex;width:100%;align-items:center;justify-content:center;gap:8px;font:inherit;font-weight:650;font-size:15px;padding:13px 16px;
border-radius:12px;border:1px solid transparent;background:var(--accent);color:var(--accent-ink);cursor:pointer;text-decoration:none;margin-top:16px}
.btn:hover{background:var(--accent-hover)}
.btn.ghost{background:transparent;color:var(--text);border-color:var(--border-strong)}.btn.ghost:hover{background:var(--bg-soft)}
.or{display:flex;align-items:center;gap:10px;color:var(--faint);font-size:12.5px;margin:20px 0 0}.or::before,.or::after{content:"";flex:1;border-top:1px solid var(--border)}
.note{font-size:12.5px;color:var(--faint);margin-top:14px;text-align:center}
.tone-ok{--tone:var(--ok);--tone-soft:var(--ok-soft)}.tone-warn{--tone:var(--warn);--tone-soft:var(--warn-soft)}
.tone-danger{--tone:var(--danger);--tone-soft:var(--danger-soft)}.tone-accent{--tone:var(--accent);--tone-soft:var(--accent-soft)}
.tone-gray{--tone:var(--gray);--tone-soft:var(--gray-soft)}
"""

ICONS = {
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "snooze": '<path d="M18 14V11a6 6 0 0 0-12 0v3l-2 3h16z"/><path d="M10 20a2 2 0 0 0 4 0"/><path d="M10 9h4l-4 4h4"/>',
    "renew": '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.2v.1"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
}


def icon(name: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>'
    )


def page_item_card(name: str, expiry: str, provider: str = "") -> str:
    big, small, tone = countdown(expiry)
    when = f"{'Expired' if big == 'Expired' else 'Expires'} {nice_date(expiry)}" if expiry else ""
    sub = " · ".join(x for x in (provider, when) if x)
    return (
        f'<div class="item tone-{tone}"><div><b>{e(name)}</b><small>{e(sub)}</small></div>'
        f'<div class="count"><b>{e(big)}</b><small>{e(small)}</small></div></div>'
    )


def page_html(title: str, body: str, *, tone: str = "accent", badge: str = "clock", app_url: str = "/", script: str = "") -> str:
    """A full page. ``body`` is trusted HTML built by the caller (escape user text with ``e``)."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="no-referrer">
<title>{e(title)} · Lapse</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="wrap">
  <a class="brand" href="{e(app_url, quote=True)}"><span class="mark">{icon("clock")}</span>Lapse</a>
  <div class="card tone-{tone}">
    <div class="badge">{icon(badge)}</div>
    {body}
  </div>
</main>
{f"<script>{script}</script>" if script else ""}
</body>
</html>"""
