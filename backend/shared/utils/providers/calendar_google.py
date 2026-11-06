"""Google Calendar adapter — written from the public docs:

  OAuth 2.0 for web server apps  https://developers.google.com/identity/protocols/oauth2/web-server
      authorize: https://accounts.google.com/o/oauth2/v2/auth  (client_id, redirect_uri, response_type=code,
                 scope, access_type=offline, prompt=consent, state)
      token:     https://oauth2.googleapis.com/token         (grant_type=authorization_code | refresh_token)
  Calendar API v3, events.insert  https://developers.google.com/calendar/api/v3/reference/events/insert
      POST  https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1&sendUpdates=all
      body: summary, description, start{dateTime,timeZone}, end{…}, attendees[{email}],
            conferenceData.createRequest{requestId, conferenceSolutionKey{type: "hangoutsMeet"}}
      response: id, htmlLink, hangoutLink (the Meet link — present only when Meet was created)
  events.patch   https://developers.google.com/calendar/api/v3/reference/events/patch   (PATCH …/events/{id})
  events.delete  https://developers.google.com/calendar/api/v3/reference/events/delete  (DELETE …/events/{id})
  Create Meet links: https://developers.google.com/calendar/api/guides/create-events#conferencing
  Scope used: https://www.googleapis.com/auth/calendar.events  (+ openid email to learn the account address)
"""
from __future__ import annotations

from urllib.parse import urlencode

from shared.utils.providers.calendar_base import CalendarAdapter, Event, EventResult, TokenSet, env, expires_in_to_at

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "https://www.googleapis.com/auth/calendar.events openid email"


def event_body(ev: Event) -> dict:
    body = {
        "summary": ev.title, "description": ev.description or "",
        "start": {"dateTime": ev.start.isoformat(), "timeZone": ev.timezone},
        "end": {"dateTime": ev.end.isoformat(), "timeZone": ev.timezone},
        "attendees": [{"email": a["email"], **({"displayName": a["name"]} if a.get("name") else {})}
                      for a in ev.attendees if a.get("email")],
    }
    if ev.location:
        body["location"] = ev.location
    if ev.online_meeting:
        body["conferenceData"] = {"createRequest": {"requestId": ev.request_id or "lexora",
                                                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


def parse_event(j: dict | None) -> EventResult:
    j = j if isinstance(j, dict) else {}
    join = j.get("hangoutLink")
    if not join:                                           # entryPoints carry the link too (video entry point)
        for ep in ((j.get("conferenceData") or {}).get("entryPoints") or []):
            if ep.get("entryPointType") == "video" and ep.get("uri"):
                join = ep["uri"]
                break
    return EventResult(True, event_id=j.get("id"), join_url=join, html_link=j.get("htmlLink"))


class GoogleCalendar(CalendarAdapter):
    key, label = "google", "Google Calendar"
    docs_url = "https://developers.google.com/calendar/api/v3/reference/events/insert"
    oauth_docs_url = "https://developers.google.com/identity/protocols/oauth2/web-server"
    env_vars = ("LEXORA_GOOGLE_CLIENT_ID", "LEXORA_GOOGLE_CLIENT_SECRET")

    def auth_url(self, redirect_uri: str, state: str) -> str:
        q = {"client_id": env("LEXORA_GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri, "response_type": "code",
             "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true"}
        return f"{AUTH_URL}?{urlencode(q)}"

    async def _token(self, data: dict, org_id: str) -> TokenSet:
        r = await self._call(org_id, "token", "POST", TOKEN_URL, data=data, retry_unsafe=True,
                             audit_request={"grant_type": data.get("grant_type")})
        if not r.ok or not isinstance(r.json, dict) or not r.json.get("access_token"):
            return TokenSet(False, error=r.error_text())
        j = r.json
        return TokenSet(True, access_token=j["access_token"], refresh_token=j.get("refresh_token"),
                        expires_at=expires_in_to_at(j.get("expires_in")), scope=j.get("scope"))

    async def exchange_code(self, code: str, redirect_uri: str, org_id: str = "") -> TokenSet:
        t = await self._token({"code": code, "client_id": env("LEXORA_GOOGLE_CLIENT_ID"),
                               "client_secret": env("LEXORA_GOOGLE_CLIENT_SECRET"), "redirect_uri": redirect_uri,
                               "grant_type": "authorization_code"}, org_id)
        if t.ok:
            u = await self._call(org_id, "userinfo", "GET", USERINFO_URL,
                                 headers={"Authorization": f"Bearer {t.access_token}"})
            if u.ok and isinstance(u.json, dict):
                t.account_email = u.json.get("email")
        return t

    async def refresh(self, refresh_token: str, org_id: str = "") -> TokenSet:
        t = await self._token({"refresh_token": refresh_token, "client_id": env("LEXORA_GOOGLE_CLIENT_ID"),
                               "client_secret": env("LEXORA_GOOGLE_CLIENT_SECRET"), "grant_type": "refresh_token"}, org_id)
        if t.ok and not t.refresh_token:
            t.refresh_token = refresh_token                 # Google does not resend it on refresh
        return t

    async def create_event(self, access_token: str, ev: Event, org_id: str = "") -> EventResult:
        r = await self._call(org_id, "create_event", "POST", EVENTS_URL, json=event_body(ev),
                             params={"conferenceDataVersion": 1, "sendUpdates": "all"},
                             headers={"Authorization": f"Bearer {access_token}"}, idempotency_key=ev.request_id or None)
        if not r.ok:
            return EventResult(False, error=r.error_text(), status=r.status)
        return parse_event(r.json)

    async def update_event(self, access_token: str, event_id: str, ev: Event, org_id: str = "") -> EventResult:
        body = event_body(ev)
        body.pop("conferenceData", None)                    # keep the Meet that already exists
        r = await self._call(org_id, "update_event", "PATCH", f"{EVENTS_URL}/{event_id}", json=body,
                             params={"conferenceDataVersion": 1, "sendUpdates": "all"},
                             headers={"Authorization": f"Bearer {access_token}"}, retry_unsafe=True)
        if not r.ok:
            return EventResult(False, error=r.error_text(), status=r.status)
        return parse_event(r.json)

    async def cancel_event(self, access_token: str, event_id: str, org_id: str = "") -> EventResult:
        r = await self._call(org_id, "cancel_event", "DELETE", f"{EVENTS_URL}/{event_id}",
                             params={"sendUpdates": "all"}, headers={"Authorization": f"Bearer {access_token}"})
        if not r.ok and r.status not in (404, 410):          # already gone counts as cancelled
            return EventResult(False, error=r.error_text(), status=r.status)
        return EventResult(True, event_id=event_id, status=r.status)
