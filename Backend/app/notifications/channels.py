"""External alert channels. Each sender returns a provider message id or raises ChannelError.

All channels are optional and configured by environment variables; unconfigured
channels are skipped. Secrets never leave the backend; targets are masked in receipts.
"""

from dataclasses import dataclass

import httpx

from app.core.config import settings


class ChannelError(Exception):
    pass


GRAPH_API = "https://graph.facebook.com/v21.0"
# Meta error codes meaning "free-form text not allowed: no open 24h customer-service window".
WHATSAPP_WINDOW_ERRORS = {131047, 131026, 470}


def whatsapp_number(raw: str) -> str:
    """E.164 digits without '+'. A bare 10-digit number is treated as Indian (+91)."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"91{digits}" if len(digits) == 10 else digits


def mask(value: str) -> str:
    return value if len(value) <= 6 else f"{value[:3]}…{value[-3:]}"


@dataclass(frozen=True)
class ChannelTarget:
    channel: str
    target: str  # raw (kept server-side only)

    @property
    def masked(self) -> str:
        return mask(self.target)


def configured_targets() -> list[ChannelTarget]:
    targets: list[ChannelTarget] = []
    if settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
        for number in filter(None, (whatsapp_number(n) for n in (settings.ALERT_WHATSAPP_TO or "").split(","))):
            targets.append(ChannelTarget("whatsapp", number))
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        targets.append(ChannelTarget("telegram", settings.TELEGRAM_CHAT_ID))
    if settings.NTFY_TOPIC:
        targets.append(ChannelTarget("ntfy", settings.NTFY_TOPIC))
    if settings.SLACK_WEBHOOK_URL:
        targets.append(ChannelTarget("slack", "slack-webhook"))
    if settings.ALERT_WEBHOOK_URL:
        targets.append(ChannelTarget("webhook", "alert-webhook"))
    return targets


NTFY_PRIORITY = {"P1": "5", "P2": "4", "P3": "3", "P4": "2"}


async def send(target: ChannelTarget, *, severity: str, title: str, body: str, link: str | None,
               transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    text = f"{title}\n{body}" + (f"\n{link}" if link else "")
    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
        if target.channel == "whatsapp":
            return await _send_whatsapp(client, target.target, text)
        if target.channel == "telegram":
            response = await client.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": target.target, "text": text[:4000], "disable_web_page_preview": True},
            )
            _raise(response)
            return str(response.json().get("result", {}).get("message_id"))
        if target.channel == "ntfy":
            headers = {"Title": title[:200].encode("ascii", "ignore").decode() or "PayFlow alert",
                       "Priority": NTFY_PRIORITY.get(severity, "3"), "Tags": "rotating_light" if severity == "P1" else "warning"}
            if link:
                headers["Click"] = link
            response = await client.post(f"{settings.NTFY_SERVER.rstrip('/')}/{target.target}",
                                         content=body.encode(), headers=headers)
            _raise(response)
            return response.json().get("id") if response.headers.get("content-type", "").startswith("application/json") else None
        if target.channel == "slack":
            response = await client.post(settings.SLACK_WEBHOOK_URL, json={"text": text})
            _raise(response)
            return None
        if target.channel == "webhook":
            response = await client.post(settings.ALERT_WEBHOOK_URL, json={
                "severity": severity, "title": title, "body": body, "link": link, "source": "payflow-ai"})
            _raise(response)
            return None
    raise ChannelError(f"Unknown channel {target.channel}")


async def _send_whatsapp(client: httpx.AsyncClient, to: str, text: str) -> str | None:
    """Meta Cloud API. Free-form text is delivered inside the 24h window that opens whenever the
    admin messages the business number; outside it Meta only accepts templates, so we fall back to
    the pre-approved `hello_world` template as a wake-up ping (the full alert stays in the console)."""
    url = f"{GRAPH_API}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    response = await client.post(url, headers=headers, json={
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
        "type": "text", "text": {"preview_url": False, "body": text[:4000]},
    })
    if response.status_code >= 400 and _meta_error_code(response) in WHATSAPP_WINDOW_ERRORS:
        response = await client.post(url, headers=headers, json={
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": "hello_world", "language": {"code": "en_US"}},
        })
    _raise(response)
    return (response.json().get("messages") or [{}])[0].get("id")


def _meta_error_code(response: httpx.Response) -> int | None:
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def _raise(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise ChannelError(f"HTTP {response.status_code}: {response.text[:200]}")
