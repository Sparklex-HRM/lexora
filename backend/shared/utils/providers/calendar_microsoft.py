"""Microsoft 365 (Outlook / Teams) calendar adapter — written from the public docs:

  Microsoft identity platform, auth code flow
      https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
      authorize: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
                 (client_id, response_type=code, redirect_uri, scope, state, response_mode=query)
      token:     https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
                 (grant_type=authorization_code | refresh_token, client_secret)
  Graph: create event  https://learn.microsoft.com/en-us/graph/api/user-post-events
      POST https://graph.microsoft.com/v1.0/me/events
      body: subject, body{contentType,content}, start{dateTime,timeZone}, end{…},
            attendees[{emailAddress{address,name}, type:"required"}],
            isOnlineMeeting:true, onlineMeetingProvider:"teamsForBusiness"
      response: id, webLink, onlineMeeting{joinUrl} (the Teams link — only when the meeting was created)
  update event  https://learn.microsoft.com/en-us/graph/api/event-update   (PATCH /me/events/{id})
  delete event  https://learn.microsoft.com/en-us/graph/api/event-delete   (DELETE /me/events/{id})
  Scopes used: offline_access Calendars.ReadWrite User.Read
"""
from __future__ import annotations

from urllib.parse import urlencode

from shared.utils.providers.calendar_base import CalendarAdapter, Event, EventResult, TokenSet, env, expires_in_to_at

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access Calendars.ReadWrite User.Read"


def tenant() -> str:
    return env("LEXORA_MS_TENANT") or "common"


def auth_base() -> str:
    return f"https://login.microsoftonline.com/{tenant()}/oauth2/v2.0"


def _graph_dt(d) -> str:
    """Graph wants a local date-time WITHOUT offset plus a separate timeZone."""
    return d.replace(tzinfo=None).isoformat(timespec="seconds")


def event_body(ev: Event) -> dict:
    body = {
        "subject": ev.title,
        "body": {"contentType": "text", "content": ev.description or ""},
        "start": {"dateTime": _graph_dt(ev.start), "timeZone": ev.timezone},
        "end": {"dateTime": _graph_dt(ev.end), "timeZone": ev.timezone},
        "attendees": [{"emailAddress": {"address": a["email"], **({"name": a["name"]} if a.get("name") else {})},
                       "type": "required"} for a in ev.attendees if a.get("email")],
    }
    if ev.location:
        body["location"] = {"displayName": ev.location}
    if ev.online_meeting:
        body["isOnlineMeeting"] = True
        body["onlineMeetingProvider"] = "teamsForBusiness"
    return body


def parse_event(j: dict | None) -> EventResult:
    j = j if isinstance(j, dict) else {}
    join = (j.get("onlineMeeting") or {}).get("joinUrl")
    return EventResult(True, event_id=j.get("id"), join_url=join, html_link=j.get("webLink"))


class MicrosoftCalendar(CalendarAdapter):
    key, label = "microsoft", "Microsoft 365 Calendar (Teams)"
    docs_url = "https://learn.microsoft.com/en-us/graph/api/user-post-events"
    oauth_docs_url = "https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow"
    env_vars = ("LEXORA_MS_CLIENT_ID", "LEXORA_MS_CLIENT_SECRET", "LEXORA_MS_TENANT")

    def auth_url(self, redirect_uri: str, state: str) -> str:
        q = {"client_id": env("LEXORA_MS_CLIENT_ID"), "response_type": "code", "redirect_uri": redirect_uri,
             "scope": SCOPES, "state": state, "response_mode": "query"}
        return f"{auth_base()}/authorize?{urlencode(q)}"

    async def _token(self, data: dict, org_id: str) -> TokenSet:
        r = await self._call(org_id, "token", "POST", f"{auth_base()}/token", data=data, retry_unsafe=True,
                             audit_request={"grant_type": data.get("grant_type")})
        if not r.ok or not isinstance(r.json, dict) or not r.json.get("access_token"):
            return TokenSet(False, error=r.error_text())
        j = r.json
        return TokenSet(True, access_token=j["access_token"], refresh_token=j.get("refresh_token"),
                        expires_at=expires_in_to_at(j.get("expires_in")), scope=j.get("scope"))

    async def exchange_code(self, code: str, redirect_uri: str, org_id: str = "") -> TokenSet:
        t = await self._token({"client_id": env("LEXORA_MS_CLIENT_ID"), "client_secret": env("LEXORA_MS_CLIENT_SECRET"),
                               "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                               "scope": SCOPES}, org_id)
        if t.ok:
            u = await self._call(org_id, "me", "GET", f"{GRAPH}/me", headers={"Authorization": f"Bearer {t.access_token}"})
            if u.ok and isinstance(u.json, dict):
                t.account_email = u.json.get("mail") or u.json.get("userPrincipalName")
        return t

    async def refresh(self, refresh_token: str, org_id: str = "") -> TokenSet:
        t = await self._token({"client_id": env("LEXORA_MS_CLIENT_ID"), "client_secret": env("LEXORA_MS_CLIENT_SECRET"),
                               "refresh_token": refresh_token, "grant_type": "refresh_token", "scope": SCOPES}, org_id)
        if t.ok and not t.refresh_token:
            t.refresh_token = refresh_token
        return t

    async def create_event(self, access_token: str, ev: Event, org_id: str = "") -> EventResult:
        r = await self._call(org_id, "create_event", "POST", f"{GRAPH}/me/events", json=event_body(ev),
                             headers={"Authorization": f"Bearer {access_token}"}, idempotency_key=ev.request_id or None)
        if not r.ok:
            return EventResult(False, error=r.error_text(), status=r.status)
        return parse_event(r.json)

    async def update_event(self, access_token: str, event_id: str, ev: Event, org_id: str = "") -> EventResult:
        body = event_body(ev)
        body.pop("isOnlineMeeting", None)
        body.pop("onlineMeetingProvider", None)             # keep the Teams meeting that already exists
        r = await self._call(org_id, "update_event", "PATCH", f"{GRAPH}/me/events/{event_id}", json=body,
                             headers={"Authorization": f"Bearer {access_token}"}, retry_unsafe=True)
        if not r.ok:
            return EventResult(False, error=r.error_text(), status=r.status)
        return parse_event(r.json)

    async def cancel_event(self, access_token: str, event_id: str, org_id: str = "") -> EventResult:
        r = await self._call(org_id, "cancel_event", "DELETE", f"{GRAPH}/me/events/{event_id}",
                             headers={"Authorization": f"Bearer {access_token}"})
        if not r.ok and r.status not in (404,):
            return EventResult(False, error=r.error_text(), status=r.status)
        return EventResult(True, event_id=event_id, status=r.status)
