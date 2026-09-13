"""Безопасные открытые проверки без CAPTCHA, логинов и обхода ограничений."""
from __future__ import annotations

import json
import socket
import subprocess
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from suppliers.verification import normalize_domain


def _json(url: str, timeout: int = 12) -> dict | list:
    request = Request(url, headers={"User-Agent": "TENDER_AI/1.0 supplier verification"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def rdap_check(url: str) -> dict:
    domain = normalize_domain(url)
    try:
        data = _json(f"https://rdap.org/domain/{quote(domain)}")
        events = {x.get("eventAction"): x.get("eventDate") for x in data.get("events", [])}
        registered = events.get("registration")
        age = None
        if registered:
            date = datetime.fromisoformat(registered.replace("Z", "+00:00"))
            age = round((datetime.now(timezone.utc) - date).days / 365.2425, 2)
        expiration = events.get("expiration")
        registrar = next((x.get("vcardArray", [None, []])[1] for x in data.get("entities", [])
                          if "registrar" in x.get("roles", [])), None)
        return {"whois_status": "available", "domain_created_at": registered,
                "domain_age_days": round(age * 365.2425) if age is not None else None,
                "domain_age_years": age, "registrar": registrar,
                "expiration_date": expiration, "source": "RDAP"}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        try:
            text = subprocess.run(["whois", domain], capture_output=True, text=True,
                                  timeout=15, check=False).stdout
            fields = {}
            for line in text.splitlines():
                if ":" not in line:
                    continue
                key, value = (x.strip() for x in line.split(":", 1))
                if key.lower() in {"created", "creation date", "paid-till", "registry expiry date", "registrar"}:
                    fields[key.lower()] = value
            created = fields.get("created") or fields.get("creation date")
            age_days = None
            if created:
                date = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - date).days
            return {"whois_status": "available" if created else "unavailable",
                    "domain_created_at": created, "domain_age_days": age_days,
                    "domain_age_years": round(age_days / 365.2425, 2) if age_days is not None else None,
                    "registrar": fields.get("registrar"),
                    "expiration_date": fields.get("paid-till") or fields.get("registry expiry date"),
                    "source": "system whois fallback", "rdap_error": type(exc).__name__}
        except (OSError, subprocess.SubprocessError, ValueError) as fallback_exc:
            return {"whois_status": "unavailable", "error": type(fallback_exc).__name__,
                    "rdap_error": type(exc).__name__, "domain_created_at": None,
                    "domain_age_days": None, "registrar": None, "expiration_date": None,
                    "source": "RDAP + WHOIS fallback"}


def wayback_check(url: str) -> dict:
    domain = normalize_domain(url)
    endpoint = ("https://web.archive.org/cdx/search/cdx?url=" + quote(domain + "/*") +
                "&output=json&filter=statuscode:200&filter=mimetype:text/html&fl=timestamp,original&collapse=timestamp:6")
    try:
        rows = _json(endpoint)
        captures = [{"timestamp": x[0], "url": x[1]} for x in rows[1:]] if rows else []
        return {"wayback_status": "available", "wayback_available": bool(captures),
                "first_snapshot": captures[0]["timestamp"] if captures else None,
                "snapshot_count": len(captures), "captures": captures[:3] + captures[-6:]}
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return {"wayback_status": "unavailable", "error": type(exc).__name__}


def wayback_theme_check(url: str, keywords: tuple[str, ...]) -> dict:
    """Берёт небольшую выборку снимков; одиночное совпадение ничего не доказывает."""
    result = wayback_check(url)
    if result.get("wayback_status") != "available" or not result.get("captures"):
        return result
    samples=[]
    for capture in result["captures"][:6]:
        snapshot=f"https://web.archive.org/web/{capture['timestamp']}id_/{capture['url']}"
        try:
            request=Request(snapshot,headers={"User-Agent":"TENDER_AI/1.0 supplier verification"})
            with urlopen(request,timeout=12) as response:
                html=response.read(400_000).decode("utf-8","ignore")
            visible=re.sub(r"<[^>]+>"," ",html).casefold()
            hits=sorted({x for x in keywords if x.casefold() in visible})
            samples.append({"timestamp":capture["timestamp"],"snapshot_url":snapshot,"keyword_hits":hits})
        except (HTTPError,URLError,TimeoutError,OSError):
            continue
    stable=sum(bool(x["keyword_hits"]) for x in samples) >= 2
    result.update({"theme_samples":samples,"same_business_history":stable,
                   "theme_status":"confirmed_positive" if stable else "inconclusive"})
    return result


def cms_check(url: str) -> dict:
    try:
        request = Request(url, headers={"User-Agent": "TENDER_AI/1.0"})
        with urlopen(request, timeout=12) as response:
            html = response.read(500_000).decode("utf-8", "ignore").lower()
        signatures = {"1C-Bitrix": ("bitrix",), "WordPress": ("wp-content", "wordpress"),
                      "OpenCart": ("catalog/view/theme", "route=common/")}
        cms = next((name for name, signs in signatures.items() if any(x in html for x in signs)), None)
        return {"cms_status": "available", "cms": cms or "unknown"}
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {"cms_status": "unavailable", "error": type(exc).__name__}


def ip_check(url: str) -> dict:
    domain = normalize_domain(url)
    try:
        addresses = sorted({x[4][0] for x in socket.getaddrinfo(domain, 443)})
        reverse = []
        for address in addresses[:3]:
            try:
                reverse.append(socket.gethostbyaddr(address)[0])
            except OSError:
                pass
        return {"ip_status": "available", "addresses": addresses, "reverse_dns": sorted(set(reverse)),
                "sites_on_same_ip": None,
                "note": "Число соседних сайтов без надёжного открытого API не определялось."}
    except OSError as exc:
        return {"ip_status": "unavailable", "error": type(exc).__name__}
