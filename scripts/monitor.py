#!/usr/bin/env python3
"""
Veille Jeux d'Argent Belgique
Scrape les sources, cherche les mots-clés, génère index.html,
crée une issue GitHub en cas de nouvelles correspondances.
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

KEYWORDS_INSTITUTIONAL = [
    'entaingroup', 'ladbrokes', 'controle', 'contrôle',
    'sanctions', 'agences', 'licences', 'entain', 'bwin',
]

KEYWORDS_PRESS = [
    'bwin', 'entain', 'ladbrokes', 'jeux de hasard',
]

# Union ordonnée pour affichage et compilation des patterns
_ALL_KEYWORDS = list(dict.fromkeys(KEYWORDS_INSTITUTIONAL + KEYWORDS_PRESS))

SOURCES = [
    # ── Institutionnel belge ───────────────────────────────────────────────────
    {
        'id': 'cour', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Cour Constitutionnelle', 'color': '#1a5276',
        'url': 'https://fr.const-court.be',
    },
    {
        'id': 'chambre', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'La Chambre des Représentants', 'color': '#1a6640',
        'url': 'https://www.lachambre.be/kvvcr/showpage.cfm?section=/flwb/recent&language=fr&cfm=/site/wwwcfm/flwb/LastDocument.cfm',
    },
    {
        'id': 'senat', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Sénat de Belgique', 'color': '#1a6640',
        'url': 'https://www.senate.be/www/?MIval=/index_senate&lang=fr',
    },
    {
        'id': 'cjh', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Gaming Commission (CJH) — Nouvelles', 'color': '#7d3c98',
        'url': 'https://www.gamingcommission.be/fr/nouvelles/nouvelles-recentes',
    },
    {
        'id': 'cjh-sanctions', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Gaming Commission (CJH) — Contrôle & Sanctions', 'color': '#7d3c98',
        'url': 'https://www.gamingcommission.be/fr/commission-des-jeux-de-hasard/controle-et-sanctions',
    },
    {
        'id': 'moniteur', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Moniteur Belge', 'color': '#2c3e50',
        'url': 'https://www.ejustice.just.fgov.be/cgi/summary.pl?language=fr',
    },
    {
        'id': 'consetat', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': "Conseil d'État", 'color': '#2c3e50',
        'url': 'https://www.raadvst-consetat.be/fr',
    },
    {
        'id': 'abc', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Autorité belge de la Concurrence', 'color': '#2c3e50',
        'url': 'https://www.abc-bma.be/fr/news',
    },
    {
        'id': 'spfjust', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'SPF Justice', 'color': '#2c3e50',
        'url': 'https://justice.belgium.be/fr/news',
    },

    # ── Presse belge francophone ───────────────────────────────────────────────
    {
        'id': 'jeuarg', 'group': 'Presse belge francophone',
        'name': 'Jeu-Argent.be', 'color': '#e67e22',
        'kw_set': 'press', 'url': 'https://www.jeu-argent.be',
    },
    {
        'id': 'medor', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Médor (investigation)', 'color': '#c0392b',
        'url': 'https://medor.coop/nos-coups/',
    },
    {
        'id': 'rtbf', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'RTBF Info', 'color': '#e53935',
        'type': 'rss',
        'url': 'https://rss.rtbf.be/article/rss/highlight_rtbf_info.xml',
    },
    {
        'id': 'soir', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Le Soir', 'color': '#1565c0',
        'type': 'rss',
        'url': 'https://www.lesoir.be/arc/outboundfeeds/rss/?outputType=xml',
    },
    {
        'id': 'lalibre', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'La Libre Belgique', 'color': '#0d47a1',
        'type': 'rss',
        'url': 'https://www.lalibre.be/arc/outboundfeeds/rss/?outputType=xml',
    },
    {
        'id': 'dhnet', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'La Dernière Heure', 'color': '#b71c1c',
        'type': 'rss',
        'url': 'https://www.dhnet.be/arc/outboundfeeds/rss/?outputType=xml',
    },
    {
        'id': 'rtlinfo', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'RTL Info', 'color': '#ff6f00',
        'type': 'rss',
        'url': 'https://feeds.rtl.be/rtlinfo_fr',
    },
    {
        'id': 'levif', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Le Vif', 'color': '#6a1b9a',
        'type': 'rss',
        'url': 'https://www.levif.be/arc/outboundfeeds/rss/?outputType=xml',
    },
    {
        'id': 'sudinfo', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Sud Info', 'color': '#e65100',
        'type': 'rss',
        'url': 'https://www.sudinfo.be/arc/outboundfeeds/rss/?outputType=xml',
    },

    # ── Presse spécialisée & Europe ───────────────────────────────────────────
    {
        'id': 'sbc', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
        'name': 'SBC News — Belgique', 'color': '#2471a3',
        'url': 'https://sbcnews.co.uk/tag/belgium/',
    },
    {
        'id': 'igaming', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
        'name': 'iGaming Business', 'color': '#2471a3',
        'url': 'https://igamingbusiness.com/?s=belgium',
    },
    {
        'id': 'casinobeats', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
        'name': 'CasinoBeats', 'color': '#2471a3',
        'url': 'https://casinobeats.com/?s=belgium',
    },
    {
        'id': 'egba', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
        'name': 'EGBA (European Gaming & Betting Assoc.)', 'color': '#1565c0',
        'url': 'https://www.egba.eu/news/',
    },
    {
        'id': 'eurlex', 'group': 'Presse spécialisée & Europe', 'kw_set': 'institutional',
        'name': 'EUR-Lex (législation UE)', 'color': '#1565c0',
        'url': 'https://eur-lex.europa.eu/search.html?text=jeux+hasard+belgique&scope=EURLEX&type=quick&lang=fr',
    },
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-BE,fr;q=0.9,en;q=0.8',
}

MAX_MATCHES_PER_SOURCE = 15
REQUEST_TIMEOUT = 25
DELAY_BETWEEN_REQUESTS = 1.5


# ── Utilitaires ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Minuscules + suppression des accents."""
    text = text.lower()
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    return text


# Patterns compilés une seule fois — mot entier uniquement (évite "contrôleurs" → "controle")
_KW_PATTERNS = {
    kw: re.compile(r'\b' + re.escape(normalize(kw)) + r'\b')
    for kw in _ALL_KEYWORDS
}

_KW_MAP = {
    'institutional': KEYWORDS_INSTITUTIONAL,
    'press': KEYWORDS_PRESS,
}


def find_keywords(text: str, kw_set: str = 'institutional') -> list:
    n = normalize(text)
    keywords = _KW_MAP.get(kw_set, KEYWORDS_INSTITUTIONAL)
    return [kw for kw in keywords if _KW_PATTERNS[kw].search(n)]


# Extraction de dates
_DATE_RE = re.compile(r'\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})\b')
_MONTHS_FR = {
    'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4,
    'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8,
    'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12,
}
_MONTH_NAME_RE = re.compile(r'\b(\d{1,2})\s+(\w+)\s+(\d{4})\b')


def extract_date(text: str):
    """Retourne un objet datetime ou None."""
    m = _DATE_RE.search(text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except Exception:
            pass
    m = _MONTH_NAME_RE.search(normalize(text))
    if m:
        month = _MONTHS_FR.get(m.group(2))
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)))
            except Exception:
                pass
    return None


# ── Scraping ───────────────────────────────────────────────────────────────────

def fetch_rss(src: dict) -> dict:
    """Récupère un flux RSS/Atom et filtre les entrées par mots-clés."""
    try:
        resp = requests.get(src['url'], headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content, 'xml')
        kw_set = src.get('kw_set', 'institutional')
        matches = []

        for item in soup.find_all('item'):
            title_tag = item.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''
            if not title:
                continue

            # URL : guid permalink en priorité, sinon link
            guid_tag = item.find('guid')
            link_tag = item.find('link')
            link = ''
            if guid_tag and guid_tag.get('isPermaLink', 'true').lower() != 'false':
                link = guid_tag.get_text(strip=True)
            if not link and link_tag:
                link = link_tag.get_text(strip=True)
            if not link:
                continue

            # Description — nettoyer le HTML éventuel
            desc_tag = item.find('description')
            desc = ''
            if desc_tag:
                raw = desc_tag.get_text(strip=True)
                desc = BeautifulSoup(raw, 'html.parser').get_text(' ', strip=True)

            # Date de publication
            pub_tag = item.find('pubDate')
            date_str = ''
            if pub_tag:
                try:
                    dt = parsedate_to_datetime(pub_tag.get_text(strip=True))
                    date_str = dt.strftime('%d/%m/%Y')
                except Exception:
                    pass

            kws = find_keywords(title + ' ' + desc, kw_set)
            if not kws:
                continue

            matches.append({
                'text': title,
                'url': link,
                'keywords': kws,
                'context': desc[:220] if desc else '',
                'date': date_str,
            })

            if len(matches) >= MAX_MATCHES_PER_SOURCE:
                break

        return {'status': 'ok', 'matches': matches}

    except requests.exceptions.Timeout:
        return {'status': 'error', 'message': f'Délai dépassé ({REQUEST_TIMEOUT}s)'}
    except requests.exceptions.ConnectionError as e:
        return {'status': 'error', 'message': f'Connexion impossible : {str(e)[:60]}'}
    except requests.exceptions.HTTPError as e:
        return {'status': 'error', 'message': f'HTTP {e.response.status_code}'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)[:80]}


def fetch_source(src: dict) -> dict:
    if src.get('type') == 'rss':
        return fetch_rss(src)
    kw_set = src.get('kw_set', 'institutional')
    try:
        resp = requests.get(
            src['url'], headers=HEADERS,
            timeout=REQUEST_TIMEOUT, allow_redirects=True,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or 'utf-8'

        soup = BeautifulSoup(resp.text, 'lxml')
        for tag in soup.find_all(['nav', 'footer', 'script', 'style', 'header']):
            tag.decompose()

        seen_texts = set()
        matches = []

        for a in soup.find_all('a', href=True):
            text = a.get_text(' ', strip=True)
            if not text or len(text) < 10 or len(text) > 350:
                continue
            if text in seen_texts:
                continue
            seen_texts.add(text)

            href = a['href'].strip()
            if not href or href.startswith('javascript') or href in ('#', ''):
                continue

            full_url = href if href.startswith('http') else urljoin(src['url'], href)

            block = a.find_parent(['article', 'li', 'tr', 'p', 'div', 'section'])
            ctx = block.get_text(' ', strip=True)[:280] if block else text

            kws = find_keywords(text + ' ' + ctx, kw_set)
            if not kws:
                continue

            date_obj = extract_date(ctx) or extract_date(text)
            date_str = date_obj.strftime('%d/%m/%Y') if date_obj else ''

            matches.append({
                'text': text,
                'url': full_url,
                'keywords': kws,
                'context': ctx if ctx != text else '',
                'date': date_str,
            })

            if len(matches) >= MAX_MATCHES_PER_SOURCE:
                break

        return {'status': 'ok', 'matches': matches}

    except requests.exceptions.Timeout:
        return {'status': 'error', 'message': f'Délai dépassé ({REQUEST_TIMEOUT}s)'}
    except requests.exceptions.ConnectionError as e:
        return {'status': 'error', 'message': f'Connexion impossible : {str(e)[:60]}'}
    except requests.exceptions.HTTPError as e:
        return {'status': 'error', 'message': f'HTTP {e.response.status_code}'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)[:80]}


# ── Génération HTML ────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))


def _parse_date(date_str: str):
    if not date_str:
        return datetime.min
    try:
        return datetime.strptime(date_str, '%d/%m/%Y')
    except Exception:
        return datetime.min


def generate_html(results: dict, run_time: str, new_count: int) -> str:
    # Comptage global par mot-clé
    kw_counts = {kw: 0 for kw in _ALL_KEYWORDS}
    for result in results.values():
        for m in result.get('matches', []):
            for kw in m.get('keywords', []):
                if kw in kw_counts:
                    kw_counts[kw] += 1

    total_alerts = sum(kw_counts.values())

    # ── Carte résumé — toutes alertes triées par date ──────────────────────────
    all_alerts = []
    for src in SOURCES:
        result = results.get(src['id'], {})
        for m in result.get('matches', []):
            all_alerts.append({
                'source_name': src['name'],
                'source_color': src['color'],
                **m,
            })
    all_alerts.sort(key=lambda a: _parse_date(a.get('date', '')), reverse=True)

    if all_alerts:
        rows = ''
        for idx, a in enumerate(all_alerts):
            tags = ''.join(f'<span class="tag">{esc(kw)}</span>' for kw in a['keywords'])
            date_badge = (
                f'<span class="alert-date">{esc(a["date"])}</span>'
                if a.get('date') else '<span class="alert-date no-date">date ?</span>'
            )
            row_id = f'ar{idx}'
            js_url   = json.dumps(a['url'])
            js_title = json.dumps(a['text'])
            js_date  = json.dumps(a.get('date', ''))
            js_src   = json.dumps(a['source_name'])
            rows += f'''<div class="alert-row" id="{row_id}">
  <div class="alert-row-main">
    {date_badge}
    <span class="alert-src" style="color:{a["source_color"]}">{esc(a["source_name"])}</span>
    <a href="{esc(a["url"])}" target="_blank">{esc(a["text"])}</a>
    <div class="tag-row">{tags}</div>
  </div>
  <button class="btn-read" onclick="markRead({js_url},{js_title},{js_date},{js_src},'{row_id}')">✓ Lu</button>
</div>'''
        summary_card = f'''<div class="card summary-card">
  <div class="card-head">
    <div class="card-title">🔔 Résumé des alertes — {total_alerts} résultat{"s" if total_alerts != 1 else ""}</div>
    <span class="badge badge-match" id="summary-badge">{total_alerts} alerte{"s" if total_alerts != 1 else ""}</span>
  </div>
  <div class="card-body" id="summary-body">{rows}</div>
</div>'''
    else:
        summary_card = '''<div class="card summary-card">
  <div class="card-head"><div class="card-title">🔔 Résumé des alertes</div></div>
  <div class="card-body"><div class="empty">Aucune alerte pour cette vérification.</div></div>
</div>'''

    # Chips mots-clés
    kw_chips = '<span class="kw-label">Institutionnel :</span>'
    for kw in KEYWORDS_INSTITUTIONAL:
        cnt = kw_counts.get(kw, 0)
        kw_chips += (f'<span class="kw-chip active">{esc(kw)} <span class="n">{cnt}</span></span>'
                     if cnt else f'<span class="kw-chip">{esc(kw)}</span>')
    kw_chips += '<span class="kw-label kw-label-press">Presse :</span>'
    for kw in KEYWORDS_PRESS:
        cnt = kw_counts.get(kw, 0)
        kw_chips += (f'<span class="kw-chip active">{esc(kw)} <span class="n">{cnt}</span></span>'
                     if cnt else f'<span class="kw-chip">{esc(kw)}</span>')

    # ── Cartes par source ──────────────────────────────────────────────────────
    cards_html = ''
    current_group = ''
    for src in SOURCES:
        grp = src.get('group', '')
        if grp != current_group:
            current_group = grp
            cards_html += f'<div class="group-label">{esc(grp)}</div>'

        result = results.get(src['id'], {'status': 'error', 'message': 'Non exécuté'})
        status = result.get('status', 'error')
        matches = result.get('matches', [])

        if status == 'ok':
            dot = 'ok'
            n = len(matches)
            badge = (f'<span class="badge badge-match">🔴 {n} alerte{"s" if n > 1 else ""}</span>'
                     if matches else '<span class="badge badge-none">0 alerte</span>')
        else:
            dot = 'err'
            badge = '<span class="badge badge-err">Erreur</span>'

        if status == 'error':
            body = f'<div class="err-msg">⚠️ {esc(result.get("message", "Erreur inconnue"))}</div>'
        elif not matches:
            body = '<div class="empty">Aucune correspondance pour ces mots-clés.</div>'
        else:
            items = ''
            for m in matches:
                tags = ''.join(f'<span class="tag">{esc(kw)}</span>' for kw in m['keywords'])
                date_html = f'<span class="item-date">{esc(m["date"])}</span> ' if m.get('date') else ''
                ctx_html = (f'<div class="ctx">{esc(m["context"][:220])}…</div>'
                            if m.get('context') else '')
                items += (f'<div class="result">'
                          f'{date_html}'
                          f'<a href="{esc(m["url"])}" target="_blank">{esc(m["text"])}</a>'
                          f'{ctx_html}'
                          f'<div class="tag-row">{tags}</div>'
                          f'</div>')
            body = items

        cards_html += f'''<div class="card" style="border-left-color:{src["color"]}">
  <div class="card-head">
    <div>
      <div class="card-title"><span class="dot {dot}"></span> {esc(src["name"])}</div>
      <a class="card-url" href="{esc(src["url"])}" target="_blank">{esc(src["url"])}</a>
    </div>
    {badge}
  </div>
  <div class="card-body">{body}</div>
</div>'''

    new_badge_header = (f' — <strong style="color:#e74c3c">'
                        f'{new_count} nouvelle{"s" if new_count != 1 else ""} depuis hier</strong>'
                        ) if new_count else ''

    css = """
:root{
  --bg:#eef0f4;--fg:#1a1a2e;--card:#fff;--border:#f2f3f5;--shadow:rgba(0,0,0,.08);
  --grp:#9aa;--grp-line:#dde0e8;--url:#b0b4bc;--empty:#c0c4cc;--ctx:#888;
  --date-bg:#eef0f4;--date-fg:#555;--item-date:#aaa;
  --tag-bg:#fff8dc;--tag-fg:#7d5a00;--foot:#bbb;
  --badge-none-bg:#eef0f4;--badge-none-fg:#999;
  --err:#d35400;--link:#1a5276;
}
[data-theme=dark]{
  --bg:#0d1117;--fg:#c9d1d9;--card:#161b22;--border:#21262d;--shadow:rgba(0,0,0,.4);
  --grp:#58a6ff;--grp-line:#21262d;--url:#484f58;--empty:#484f58;--ctx:#6e7681;
  --date-bg:#21262d;--date-fg:#8b949e;--item-date:#6e7681;
  --tag-bg:#2d2a1f;--tag-fg:#d4a843;--foot:#484f58;
  --badge-none-bg:#21262d;--badge-none-fg:#6e7681;
  --err:#f85149;--link:#58a6ff;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--fg);font-size:14px;transition:background .2s,color .2s}
header{background:linear-gradient(135deg,#0d1b2a,#1b3a5c);color:#fff;padding:16px 20px 12px;position:relative}
[data-theme=dark] header{background:linear-gradient(135deg,#010409,#0d1b2a)}
header h1{font-size:17px;font-weight:700;margin-bottom:4px}
#ts{font-size:11px;color:rgba(255,255,255,.55)}
.kw-row{margin-top:6px;display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.kw-label{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.4);margin-right:4px}
.kw-label-press{margin-left:10px}
.kw-chip{font-size:10px;font-weight:600;background:rgba(255,255,255,.12);border-radius:4px;padding:2px 8px;color:rgba(255,255,255,.8)}
.kw-chip.active{background:rgba(231,76,60,.3)}
.kw-chip .n{display:inline-block;background:#e74c3c;color:#fff;border-radius:8px;padding:0 4px;margin-left:5px;font-size:9px}
#theme-btn{position:absolute;top:14px;right:14px;background:rgba(255,255,255,.12);border:none;color:#fff;border-radius:6px;padding:5px 11px;font-size:11px;cursor:pointer;transition:background .2s}
#theme-btn:hover{background:rgba(255,255,255,.22)}
.page{padding:14px;display:flex;flex-direction:column;gap:10px}
.group-label{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:var(--grp);padding:6px 2px 2px;border-bottom:1px solid var(--grp-line);margin-bottom:2px}
.card{background:var(--card);border-radius:8px;box-shadow:0 1px 3px var(--shadow);border-left:4px solid #ccc;overflow:hidden;transition:background .2s}
.summary-card{border-left:6px solid #e74c3c}
.archive-card{border-left:4px solid #666}
.card-head{display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--border);gap:10px}
.card-title{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:#ccc;flex-shrink:0;display:inline-block}
.dot.ok{background:#2ecc71}.dot.err{background:#e67e22}
.card-url{font-size:10px;color:var(--url);margin-top:1px;text-decoration:none;display:block}
.card-url:hover{color:#3498db}
.badge{font-size:10px;font-weight:700;padding:3px 10px;border-radius:10px;white-space:nowrap}
.badge-match{background:#fde8e8;color:#c0392b}
.badge-none{background:var(--badge-none-bg);color:var(--badge-none-fg)}
.badge-err{background:#fff3e6;color:var(--err)}
.card-body{padding:10px 14px 12px}
.alert-row{padding:8px 0;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:8px}
.alert-row:last-child{border-bottom:none}
.alert-row-main{flex:1;min-width:0;line-height:1.6}
.alert-row a{font-size:12.5px;font-weight:600;color:var(--link);text-decoration:none}
.alert-row a:hover{text-decoration:underline}
.alert-date{font-size:10px;font-weight:700;background:var(--date-bg);color:var(--date-fg);border-radius:3px;padding:1px 7px;margin-right:6px;white-space:nowrap}
.alert-date.no-date{color:var(--empty)}
.alert-src{font-size:10px;font-weight:700;margin-right:8px}
.btn-read{flex-shrink:0;font-size:10px;font-weight:600;background:rgba(46,204,113,.12);color:#27ae60;border:none;border-radius:4px;padding:3px 9px;cursor:pointer;white-space:nowrap;margin-top:2px}
[data-theme=dark] .btn-read{color:#3fb950;background:rgba(63,185,80,.12)}
.btn-read:hover{opacity:.75}
.result{padding:8px 0;border-bottom:1px solid var(--border);line-height:1.45}
.result:last-child{border-bottom:none}
.result a{font-size:12.5px;font-weight:600;color:var(--link);text-decoration:none;display:block}
.result a:hover{text-decoration:underline}
.item-date{font-size:10px;color:var(--item-date);margin-right:6px}
.ctx{font-size:11px;color:var(--ctx);margin-top:3px;line-height:1.4}
.tag-row{margin-top:4px;display:flex;gap:4px;flex-wrap:wrap}
.tag{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:1px 6px;border-radius:3px;background:var(--tag-bg);color:var(--tag-fg)}
.empty{font-size:11.5px;color:var(--empty);padding:4px 0;font-style:italic}
.err-msg{font-size:11.5px;color:var(--err);padding:4px 0}
.archive-row{padding:5px 0;border-bottom:1px solid var(--border);font-size:11.5px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.archive-row:last-child{border-bottom:none}
.archive-row a{color:var(--ctx);text-decoration:none}
.archive-row a:hover{text-decoration:underline}
.archive-date{font-size:10px;color:var(--date-fg);background:var(--date-bg);border-radius:3px;padding:1px 6px;white-space:nowrap;flex-shrink:0}
.archive-src{font-size:10px;font-weight:700;flex-shrink:0}
.about-section{background:var(--card);border-radius:8px;border:1px solid var(--border);overflow:hidden}
.about-toggle{width:100%;background:none;border:none;padding:12px 14px;text-align:left;font-size:12px;font-weight:700;color:var(--fg);cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.about-chevron{font-size:10px;transition:transform .2s;color:var(--url)}
.about-toggle.open .about-chevron{transform:rotate(180deg)}
.about-body{display:none;padding:4px 14px 14px;font-size:12px;line-height:1.8;color:var(--ctx)}
.about-body.open{display:block}
.about-body p{margin-top:8px}
.about-body strong{color:var(--fg)}
footer{text-align:center;font-size:10px;color:var(--foot);padding:10px 16px 20px}
"""

    js = """
const KEY_THEME = 'bgw_theme';
const KEY_READ  = 'bgw_read';

// ── Thème ──────────────────────────────────────────────────────────────────────
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  const btn = document.getElementById('theme-btn');
  if(btn) btn.textContent = t==='dark' ? '☀️ Clair' : '🌙 Sombre';
}
function toggleTheme(){
  const t = document.documentElement.getAttribute('data-theme')==='dark' ? 'light' : 'dark';
  localStorage.setItem(KEY_THEME, t);
  applyTheme(t);
}

// ── Marquer comme lu ───────────────────────────────────────────────────────────
function markRead(url, title, date, src, rowId){
  const items = JSON.parse(localStorage.getItem(KEY_READ)||'[]');
  if(!items.find(i=>i.url===url)){
    items.unshift({url, title, date, src,
      readAt: new Date().toLocaleDateString('fr-BE',{day:'2-digit',month:'2-digit',year:'numeric'})});
    localStorage.setItem(KEY_READ, JSON.stringify(items.slice(0,300)));
  }
  const row = document.getElementById(rowId);
  if(row){ row.style.opacity='0'; row.style.transition='opacity .3s'; setTimeout(()=>row.remove(),300); }
  renderArchive();
}

// ── Archive ────────────────────────────────────────────────────────────────────
function renderArchive(){
  const items = JSON.parse(localStorage.getItem(KEY_READ)||'[]');
  const body  = document.getElementById('archive-body');
  const badge = document.getElementById('archive-badge');
  if(!body) return;
  if(badge) badge.textContent = items.length ? items.length+' élément'+(items.length>1?'s':'') : 'vide';
  if(!items.length){
    body.innerHTML = '<div class="empty">Aucun élément archivé.</div>';
    return;
  }
  body.innerHTML = items.map(i=>`
    <div class="archive-row">
      <span class="archive-date">${i.date||i.readAt||'—'}</span>
      <span class="archive-src" style="color:#888">${i.src}</span>
      <a href="${i.url}" target="_blank">${i.title}</a>
    </div>`).join('');
}

// ── Section À propos ──────────────────────────────────────────────────────────
function toggleAbout(){
  const btn  = document.getElementById('about-btn');
  const body = document.getElementById('about-body');
  btn.classList.toggle('open');
  body.classList.toggle('open');
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function(){
  applyTheme(localStorage.getItem(KEY_THEME)||'light');
  renderArchive();
});
"""

    return f"""<!DOCTYPE html>
<html lang="fr" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Veille — Jeux d'Argent Belgique</title>
<style>{css}</style>
</head>
<body>
<header>
  <button id="theme-btn" onclick="toggleTheme()">🌙 Sombre</button>
  <h1>🇧🇪 Veille — Jeux d'Argent Belgique</h1>
  <div id="ts">Dernière vérification : {esc(run_time)} — {total_alerts} alerte{"s" if total_alerts != 1 else ""} au total{new_badge_header}</div>
  <div class="kw-row">{kw_chips}</div>
</header>

<div class="page">

{summary_card}

{cards_html}

<div class="card archive-card">
  <div class="card-head">
    <div class="card-title">📁 Alertes déjà lues</div>
    <span class="badge badge-none" id="archive-badge">vide</span>
  </div>
  <div class="card-body" id="archive-body"><div class="empty">Aucun élément archivé.</div></div>
</div>

<div class="about-section">
  <button class="about-toggle" id="about-btn" onclick="toggleAbout()">
    ℹ️ Comment fonctionne cette page ?
    <span class="about-chevron">▼</span>
  </button>
  <div class="about-body" id="about-body">
    <p>Cette page est un <strong>tableau de bord de veille réglementaire</strong> sur le secteur des jeux d'argent en Belgique, généré automatiquement chaque matin à <strong>7h30</strong>.</p>
    <p><strong>Sources institutionnelles</strong> (Cour Constitutionnelle, La Chambre, Sénat, Gaming Commission, Moniteur Belge, Conseil d'État, ABC, SPF Justice, EUR-Lex) : surveillance des mots-clés <em>entain, entaingroup, ladbrokes, bwin, controle, sanctions, agences, licences</em>.</p>
    <p><strong>Sources presse</strong> (RTBF, Le Soir, La Libre, DH, RTL Info, Le Vif, Sud Info, Médor, SBC News, iGaming Business, CasinoBeats, EGBA) : surveillance via flux RSS des mots-clés <em>bwin, entain, ladbrokes, jeux de hasard</em>.</p>
    <p>Le bouton <strong>✓ Lu</strong> sur chaque alerte la déplace dans la carte « Alertes déjà lues » ci-dessus. Ces données sont stockées localement dans votre navigateur.</p>
    <p>Infrastructure : <strong>GitHub Actions</strong> exécute le script Python · <strong>GitHub Pages</strong> héberge cette page · LANCELLE 2026.</p>
  </div>
</div>

</div>
<footer>Veille Jeux d'Argent Belgique · LANCELLE 2026 · Généré automatiquement par GitHub Actions</footer>
<script>{js}</script>
</body>
</html>"""


# ── Issue GitHub ───────────────────────────────────────────────────────────────

def create_github_issue(new_matches_by_src: dict, run_time: str) -> None:
    token = os.environ.get('GITHUB_TOKEN')
    repo  = os.environ.get('GITHUB_REPOSITORY')

    if not token or not repo:
        print('⚠️  GITHUB_TOKEN / GITHUB_REPOSITORY absent — issue non créée')
        return

    lines = [f'Vérification du **{run_time}**\n']
    for src_name, matches in new_matches_by_src.items():
        lines.append(f'\n### {src_name}')
        for m in matches:
            kws = ', '.join(f'`{kw}`' for kw in m['keywords'])
            date_info = f' ({m["date"]})' if m.get('date') else ''
            lines.append(f'- [{m["text"]}]({m["url"]}) — {kws}{date_info}')

    total = sum(len(v) for v in new_matches_by_src.values())
    title = (f'🔴 {total} nouvelle{"s" if total != 1 else ""} '
             f'correspondance{"s" if total != 1 else ""} — {run_time}')

    resp = requests.post(
        f'https://api.github.com/repos/{repo}/issues',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
        },
        json={'title': title, 'body': '\n'.join(lines), 'labels': ['alerte']},
        timeout=15,
    )

    if resp.status_code == 201:
        print(f'✅ Issue créée : {resp.json()["html_url"]}')
    else:
        print(f'⚠️  Erreur issue : {resp.status_code} — {resp.text[:200]}')


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    data_path = Path('data/last_results.json')
    data_path.parent.mkdir(exist_ok=True)

    prev_urls: set = set()
    if data_path.exists():
        try:
            prev = json.loads(data_path.read_text(encoding='utf-8'))
            prev_urls = set(prev.get('urls', []))
        except Exception:
            pass

    print(f'🔍 Veille démarrée — {len(SOURCES)} sources, mots-clés : {", ".join(_ALL_KEYWORDS)}\n')

    results: dict = {}
    current_urls: set = set()
    new_matches_by_src: dict = {}

    for src in SOURCES:
        print(f'  → {src["name"]} … ', end='', flush=True)
        result = fetch_source(src)
        results[src['id']] = result

        if result['status'] == 'ok':
            n = len(result['matches'])
            print(f'✅ {n} correspondance{"s" if n != 1 else ""}')
            new_for_src = []
            for m in result['matches']:
                current_urls.add(m['url'])
                if m['url'] not in prev_urls:
                    new_for_src.append(m)
            if new_for_src:
                new_matches_by_src[src['name']] = new_for_src
        else:
            print(f'❌ {result["message"]}')

        time.sleep(DELAY_BETWEEN_REQUESTS)

    run_time = datetime.now(timezone.utc).strftime('%d/%m/%Y à %H:%M UTC')
    new_count = sum(len(v) for v in new_matches_by_src.values())

    print(f'\n📊 Résumé : {new_count} nouvelle{"s" if new_count != 1 else ""} correspondance{"s" if new_count != 1 else ""}')

    html = generate_html(results, run_time, new_count)
    Path('index.html').write_text(html, encoding='utf-8')
    print('✅ index.html généré')

    data_path.write_text(
        json.dumps({'urls': sorted(current_urls), 'last_run': run_time},
                   ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print('✅ data/last_results.json mis à jour')

    if new_matches_by_src:
        print(f'\n🔔 {new_count} nouvelle(s) — création d\'une issue GitHub…')
        create_github_issue(new_matches_by_src, run_time)
    else:
        print('\nℹ️  Aucune nouvelle correspondance — pas d\'issue créée')


if __name__ == '__main__':
    main()
