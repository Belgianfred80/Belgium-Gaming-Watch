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
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Configuration ──────────────────────────────────────────────────────────────

KEYWORDS_INSTITUTIONAL = [
    'entaingroup', 'ladbrokes', 'controle', 'contrôle',
    'sanctions', 'agences', 'licences', 'entain', 'bwin', 'jeux de hasard',
]

KEYWORDS_PRESS = [
    'bwin', 'entain', 'ladbrokes', 'jeux de hasard',
]

# Union ordonnée pour affichage et compilation des patterns
_ALL_KEYWORDS = list(dict.fromkeys(KEYWORDS_INSTITUTIONAL + KEYWORDS_PRESS))

# Termes de recherche envoyés séparément à chaque moteur de recherche
SEARCH_TERMS = ['ladbrokes', 'entain', 'bwin', 'jeux de hasard']

SOURCES = [
    # ── Institutionnel belge ───────────────────────────────────────────────────
    {
        # SPA Nuxt/Vuetify — l'URL ne pilote PAS la recherche (le param ?search= est ignoré).
        # Il faut remplir le champ « Recherche(s) » et cliquer SOUMETTRE.
        'id': 'cour', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Cour Constitutionnelle', 'color': '#1a5276',
        'js': True,
        'no_kw_filter': True,
        'playwright_timeout': 60_000,
        'url': 'https://fr.const-court.be/search/full-text-judgment',
        'search_terms': SEARCH_TERMS,
        'fill_js': (
            "(term) => {"
            "  const inputs = Array.from(document.querySelectorAll('input[type=\"text\"],input:not([type]),textarea'))"
            "    .filter(i => i.offsetParent !== null);"
            "  if (!inputs.length) return false;"
            "  const input = inputs[0];"
            "  const proto = input.tagName === 'TEXTAREA'"
            "    ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;"
            "  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;"
            "  input.focus();"
            "  setter.call(input, term);"
            "  input.dispatchEvent(new Event('input',  {bubbles:true}));"
            "  input.dispatchEvent(new Event('change', {bubbles:true}));"
            "  return true;"
            "}"
        ),
        'submit_js': (
            "() => {"
            "  const btn = Array.from(document.querySelectorAll('button,input[type=\"submit\"],a'))"
            "    .find(b => /soumettre|submit|rechercher/i.test((b.value||'') + ' ' + (b.textContent||'')));"
            "  if (btn) { btn.click(); return true; }"
            "  return false;"
            "}"
        ),
        'eval_extract': (
            "() => {"
            "  const hasRef = el => /\\d{4}-\\d{3}/.test(el.innerText || '');"
            "  const cards = Array.from(document.querySelectorAll('div,article,li'))"
            "    .filter(el => {"
            "      const t = el.innerText || '';"
            "      return /\\d{2}\\/\\d{2}\\/\\d{4}/.test(t) && /\\d{4}-\\d{3}/.test(t)"
            "             && t.length > 40 && t.length < 1500"
            "             && !Array.from(el.querySelectorAll('div,article,li')).some(hasRef);"
            "    });"
            "  const seen = new Set(); const out = [];"
            "  cards.forEach(c => {"
            "    const a = c.querySelector('a[href]');"
            "    const href = a ? a.href : location.href;"
            "    const text = c.innerText.trim().slice(0,300);"
            "    const key = href + '|' + text.slice(0,50);"
            "    if (seen.has(key)) return; seen.add(key);"
            "    out.push({ href, text });"
            "  });"
            "  return out;"
            "}"
        ),
    },
    # Sénat de Belgique — bloqué par Cloudflare WAF (même en navigation manuelle)
    # {
    #     'id': 'senat', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
    #     'name': 'Sénat de Belgique', 'color': '#1a6640',
    #     'js': True,
    #     'url': 'https://www.senate.be/www/webdriver?MIval=publications/recherchePublications&LANG=fr&TREFWOORDEN=ladbrokes+entain+bwin',
    # },
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
        # Formulaire POST : /cgi/rech.pl → résultats sur /cgi/rech_res.pl
        # Le champ « Une expression exacte » est rempli via fill_js, puis submit.
        'id': 'moniteur', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Moniteur Belge', 'color': '#2c3e50',
        'js': True,
        'no_kw_filter': True,
        'playwright_timeout': 60_000,
        'url': 'https://www.ejustice.just.fgov.be/cgi/rech.pl?language=fr',
        'search_terms': SEARCH_TERMS,
        'fill_js': (
            "(term) => {"
            "  const norm = s => (s||'').replace(/\\s+/g,' ').trim().toLowerCase();"
            "  let input = document.querySelector('input[name=\"exp\"]');"
            "  if (!input) {"
            "    const nodes = Array.from(document.querySelectorAll('label,td,th,div,span,p'));"
            "    const lbl = nodes.find(e => norm(e.textContent).startsWith('une expression exacte'));"
            "    if (lbl) {"
            "      let scope = lbl.closest('div,td,tr,fieldset,form');"
            "      for (let i=0; i<4 && scope && !input; i++) {"
            "        input = scope.querySelector('input[type=\"text\"],input:not([type])');"
            "        scope = scope.parentElement;"
            "      }"
            "    }"
            "  }"
            "  if (!input) return false;"
            "  input.focus(); input.value = term;"
            "  input.dispatchEvent(new Event('input',  {bubbles:true}));"
            "  input.dispatchEvent(new Event('change', {bubbles:true}));"
            "  return true;"
            "}"
        ),
        'submit_js': (
            "() => {"
            "  const btn = Array.from(document.querySelectorAll("
            "    'button,input[type=\"submit\"],input[type=\"button\"],a'))"
            "    .find(b => /rechercher|zoeken|search/i.test(b.value || b.textContent || ''));"
            "  if (btn) { btn.click(); return true; }"
            "  const f = document.querySelector('form'); if (f) { f.submit(); return true; }"
            "  return false;"
            "}"
        ),
        'eval_extract': (
            "() => Array.from(document.querySelectorAll('a[href]'))"
            "  .filter(a => /article\\.pl|numac|rech_res|\\/cgi\\//i.test(a.getAttribute('href')||''))"
            "  .map(a => {"
            "    const blk = a.closest('li,tr,article,div') || a;"
            "    return { href: a.href, text: (blk.innerText||a.innerText).trim().slice(0,300) };"
            "  })"
            "  .filter(x => x.text.length > 25)"
        ),
    },
    # Conseil d'État — bloqué par Cloudflare WAF (IP GitHub Actions blacklistée)
    # {
    #     'id': 'consetat', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
    #     'name': "Conseil d'État", 'color': '#2c3e50',
    #     'js': True,
    #     'url': 'https://www.raadvst-consetat.be/fr/jurisprudence/recherche?query={term}',
    #     'search_terms': SEARCH_TERMS,
    # },
    {
        # Drupal — résultats dans .view-content, attendre le chargement JS
        # URL filtrée sur content_type:decision pour éviter le bruit
        'id': 'abc', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'Autorité belge de la Concurrence', 'color': '#2c3e50',
        'js': True,
        'no_kw_filter': True,
        'playwright_timeout': 60_000,
        'url': 'https://www.abc-bma.be/fr/search?search_api_fulltext={term}&f%5B0%5D=content_type%3Adecision',
        'search_terms': SEARCH_TERMS,
        'wait_selector': '.view-content a, h2 a, h3 a, .views-row a',
        'eval_extract': (
            "() => {"
            "  const sel = '.view-content a[href], h2 a[href], h3 a[href], article a[href]';"
            "  const seen = new Set(); const out = [];"
            "  Array.from(document.querySelectorAll(sel)).forEach(a => {"
            "    if (!a.href.includes('abc-bma')) return;"
            "    const title = (a.innerText || '').trim();"
            "    if (title.length < 15) return;"
            "    /* Dédoublonnage sur le titre : le même dossier apparaît sous plusieurs URLs */"
            "    const key = title.toLowerCase().slice(0,80);"
            "    if (seen.has(key)) return; seen.add(key);"
            "    /* Remonter chercher la date, absente du titre du lien */"
            "    let blk = a.closest('.views-row, article, li, .node') || a.parentElement;"
            "    let ctx = '';"
            "    for (let i = 0; i < 3 && blk; i++) {"
            "      const s = (blk.innerText || '').trim();"
            "      if (s.length > title.length && s.length < 800) { ctx = s; break; }"
            "      blk = blk.parentElement;"
            "    }"
            "    const hasDate = /\\d{1,2}[\\/\\-\\.]\\d{1,2}[\\/\\-\\.]\\d{4}|\\d{1,2}\\.?\\s+\\w+\\s+\\d{4}/.test(ctx);"
            "    out.push({ href: a.href, text: (hasDate ? ctx : title).slice(0,300) });"
            "  });"
            "  return out;"
            "}"
        ),
    },
    {
        'id': 'spfjust', 'group': 'Institutionnel belge', 'kw_set': 'institutional',
        'name': 'SPF Justice', 'color': '#2c3e50',
        'type': 'rss',
        'url': 'https://justice.belgium.be/fr/news/rss',
    },

    # ── Presse belge francophone ───────────────────────────────────────────────
    {
        'id': '7sur7', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': '7sur7', 'color': '#c0392b',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'url': 'https://www.7sur7.be/recherche/?query={term}',
        'search_terms': SEARCH_TERMS,
    },
    {
        # Jetons form_build_id / form_id retirés : ils expirent et sont facultatifs
        # Extraction ciblée sur les articles, comme pour la RTBF
        'id': 'soir', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Le Soir', 'color': '#1565c0',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'playwright_timeout': 60_000,
        'url': 'https://www.lesoir.be/archives/recherche?word={term}&sort=date%20desc&datefilter=lastyear',
        'search_terms': SEARCH_TERMS,
        'eval_extract': (
            "(term) => {"
            "  const t = (term || '').toLowerCase();"
            "  const seen = new Set(); const out = [];"
            "  document.querySelectorAll('a[href]').forEach(a => {"
            "    const h = a.getAttribute('href') || '';"
            "    if (/recherche|abonnement|s-abonner|login|newsletter|podcast|\\/tag\\//i.test(h)) return;"
            "    const title = (a.innerText || '').trim();"
            "    if (title.length < 20) return;"
            "    let blk = a, ctx = '';"
            "    for (let i = 0; i < 4 && blk; i++) {"
            "      const s = (blk.innerText || '').trim();"
            "      if (s.length > title.length + 50 && s.length < 1400) { ctx = s; break; }"
            "      blk = blk.parentElement;"
            "    }"
            "    const full = (title + ' ' + ctx).toLowerCase();"
            "    if (t && !full.includes(t)) return;"
            "    if (seen.has(a.href)) return; seen.add(a.href);"
            "    out.push({ href: a.href,"
            "               text: (ctx || title).replace(/\\s+/g,' ').slice(0,300) });"
            "  });"
            "  return out;"
            "}"
        ),
    },
    {
        # 5 flux RSS agrégés en une seule source
        'id': 'rtbf', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'RTBF Info', 'color': '#e53935',
        'type': 'rss',
        'url': 'https://rss.rtbf.be/article/rss/highlight_rtbf_info.xml?source=internal',
        'feeds': [
            'https://rss.rtbf.be/article/rss/highlight_rtbf_info.xml?source=internal',
            'https://rss.rtbf.be/article/rss/highlight_rtbf_info-regions.xml?source=internal',
            'https://rss.rtbf.be/article/rss/highlight_rtbf_monde-europe.xml?source=internal',
            'https://rss.rtbf.be/article/rss/highlight_rtbf_info-economie.xml?source=internal',
            'https://rss.rtbf.be/article/rss/highlight_rtbf_investigation.xml?source=internal',
        ],
    },
    # Le Soir — RSS Arc retourne 403, site JS anti-bot → commenté temporairement
    # {
    #     'id': 'soir', 'group': 'Presse belge francophone', 'kw_set': 'press',
    #     'name': 'Le Soir', 'color': '#1565c0',
    #     'type': 'rss',
    #     'url': 'https://www.lesoir.be/arc/outboundfeeds/rss/?outputType=xml',
    # },
    {
        # Complète les flux RSS : remonte les archives, pas seulement l'actualité du jour
        # SPA : extraction ciblée sur les cartes d'articles (/article/…)
        'id': 'rtbf-search', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'RTBF — Recherche', 'color': '#e53935',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'playwright_timeout': 60_000,
        'url': 'https://www.rtbf.be/recherche/article?q={term}',
        'search_terms': SEARCH_TERMS,
        'wait_selector': 'a[href*="/article/"]',
        'eval_extract': (
            "(term) => {"
            "  const t = (term || '').toLowerCase();"
            "  const seen = new Set(); const out = [];"
            "  document.querySelectorAll('a[href*=\"/article/\"]').forEach(a => {"
            "    if (seen.has(a.href)) return;"
            "    const title = (a.innerText || '').trim();"
            "    /* Remonter jusqu'a la carte pour recuperer chapeau + date */"
            "    let blk = a, ctx = '';"
            "    for (let i = 0; i < 4 && blk; i++) {"
            "      const s = (blk.innerText || '').trim();"
            "      if (s.length > 60 && s.length < 1200) { ctx = s; break; }"
            "      blk = blk.parentElement;"
            "    }"
            "    const full = (title + ' ' + ctx).trim();"
            "    if (full.length < 30) return;"
            "    if (t && !full.toLowerCase().includes(t)) return;"
            "    seen.add(a.href);"
            "    out.push({ href: a.href, text: (ctx || title).replace(/\\s+/g,' ').slice(0,300) });"
            "  });"
            "  return out;"
            "}"
        ),
    },
    {
        'id': 'lalibre', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'La Libre Belgique', 'color': '#0d47a1',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'url': 'https://www.lalibre.be/recherche/query:{term};/',
        'search_terms': SEARCH_TERMS,
    },
    {
        'id': 'dhnet', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'La Dernière Heure', 'color': '#b71c1c',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'url': 'https://www.dhnet.be/recherche/query:{term};/',
        'search_terms': SEARCH_TERMS,
    },
    {
        'id': 'rtlinfo', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'RTL Info', 'color': '#ff6f00',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'url': 'https://www.rtl.be/archives/recherche?word={term}',
        'search_terms': SEARCH_TERMS,
    },
    {
        'id': 'sudinfo', 'group': 'Presse belge francophone', 'kw_set': 'press',
        'name': 'Sud Info', 'color': '#e65100',
        'js': True,
        'no_kw_filter': True, 'require_term': True,
        'url': 'https://www.sudinfo.be/archives/recherche?word={term}&sort=date+desc&datefilter=lastyear',
        'search_terms': SEARCH_TERMS,
    },

    # ── Presse spécialisée & Europe ───────────────────────────────────────────
    # SBC News — bloqué par Cloudflare WAF
    # {
    #     'id': 'sbc', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
    #     'name': 'SBC News — Belgique', 'color': '#2471a3',
    #     'js': True,
    #     'url': 'https://sbcnews.co.uk/tag/belgium/',
    # },
    {
        'id': 'casinobeats', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
        'name': 'CasinoBeats', 'color': '#2471a3',
        'js': True,
        'no_kw_filter': True,
        'url': 'https://casinobeats.com/?s={term}',
        'search_terms': SEARCH_TERMS,
    },
    # EGBA — domaine mort (ERR_NAME_NOT_RESOLVED sur www.egba.eu et egba.eu)
    # {
    #     'id': 'egba', 'group': 'Presse spécialisée & Europe', 'kw_set': 'press',
    #     'name': 'EGBA (European Gaming & Betting Assoc.)', 'color': '#1565c0',
    #     'js': True,
    #     'url': 'https://egba.eu/news/',
    # },
    {
        # js:True indispensable — sans lui eval_extract est ignoré et la page
        # entière est aspirée (liens « Create in My RSS alerts », ELI, etc.)
        'id': 'eurlex', 'group': 'Presse spécialisée & Europe', 'kw_set': 'institutional',
        'name': 'EUR-Lex (législation UE)', 'color': '#1565c0',
        'js': True,
        'no_kw_filter': True,
        'url': 'https://eur-lex.europa.eu/search.html?text={term}+belgique&scope=EURLEX&type=quick&lang=fr',
        'search_terms': SEARCH_TERMS,
        'eval_extract': (
            "() => {"
            "  const seen = new Set(); const out = [];"
            "  document.querySelectorAll('a[href*=\"uri=CELEX\"]').forEach(a => {"
            "    let t = (a.innerText || '').trim();"
            "    if (t.length < 25) return;"
            "    if (/^https?:\\/\\//i.test(t)) return;"
            "    if (/rss|s'?abonner|subscribe/i.test(t)) return;"
            "    const cx = (a.href.match(/CELEX[:%3A]*([A-Z0-9()]+)/i) || [])[1] || a.href;"
            "    if (seen.has(cx)) return; seen.add(cx);"
            "    t = t.replace(/\\s*Select:\\s*\\d+\\s*/gi, ' ').replace(/\\s+/g, ' ').trim();"
            "    out.push({ href: a.href, text: t.slice(0,300) });"
            "  });"
            "  return out;"
            "}"
        ),
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

MIN_YEAR = 2024          # Rejeter les résultats antérieurs à cette année
MAX_MATCHES_PER_SOURCE = 15
REQUEST_TIMEOUT = 25
DELAY_BETWEEN_REQUESTS = 0.3   # entre les termes d'une même source
MAX_WORKERS = 8                # sources traitées en parallèle


# ── Utilitaires ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Minuscules + suppression des accents."""
    text = text.lower()
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    return text


# Paramètres de session à retirer avant dédoublonnage : le même document
# revient avec un qid/rid différent à chaque requête (EUR-Lex notamment).
_VOLATILE_PARAMS = ('qid', 'rid', 'callingUrl', 'towardUrl', 'form_build_id')


def canon_url(url: str) -> str:
    """Clé stable pour dédoublonner : sans paramètres volatils ni ancre."""
    if not url:
        return ''
    base = url.split('#', 1)[0]
    if '?' not in base:
        return base
    path, _, query = base.partition('?')
    keep = [p for p in query.split('&')
            if p and p.split('=', 1)[0] not in _VOLATILE_PARAMS]
    return path + ('?' + '&'.join(keep) if keep else '')


# Textes boilerplate à ignorer pour les sources no_kw_filter
_BOILERPLATE = frozenset([
    'skip to main content', 'aller au contenu', 'aller au menu',
    'politique des cookies', 'politique en matière de cookies',
    'enable javascript', 'how to enable javascript', 'activer javascript',
    'link is external', 'lien externe', 'lien interne',
    'advanced search', 'recherche avancée',
    'export selection', 'export all', 'clear selection',
    'customise shown information',
    'ray id', 'performance and security by cloudflare',
    'www.belgium.be', 'other information and services',
    'create in my rss alerts', 'my rss alerts', 'save to my items',
    'permanent link', 'lien permanent', 'download notice',
])

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
# Radicaux de mois sur 4 caractères (FR, NL, DE, EN) — couvre aussi les
# abréviations « juil. », « févr. », « sept. ».
# 4 caractères permet de distinguer juin/juillet et juni/juli.
_MONTH_STEMS4 = {
    'janv': 1, 'janu': 1, 'jan ': 1,
    'fevr': 2, 'febr': 2,
    'mars': 3, 'maar': 3, 'marz': 3, 'marc': 3,
    'avri': 4, 'apri': 4,
    'juin': 6, 'juni': 6, 'june': 6,
    'juil': 7, 'juli': 7, 'july': 7,
    'aout': 8, 'augu': 8,
    'sept': 9,
    'octo': 10, 'okto': 10,
    'nove': 11,
    'dece': 12, 'deze': 12, 'dec ': 12,
}
# Mois courts (3 caractères) traités à part pour éviter les collisions
_MONTH_SHORT = {'mai': 5, 'mei': 5, 'may': 5, 'jan': 1, 'feb': 2, 'fev': 2,
                'mar': 3, 'apr': 4, 'avr': 4, 'jun': 6, 'jul': 7, 'aou': 8,
                'aug': 8, 'sep': 9, 'oct': 10, 'okt': 10, 'nov': 11, 'dec': 12}


def _month_from_word(word: str):
    """Reconnaît un mois dans n'importe laquelle des 4 langues, abrégé ou non."""
    w = normalize(word).strip('.')
    if not w:
        return None
    return _MONTH_STEMS4.get(w[:4]) or _MONTH_SHORT.get(w[:3])


# « 11 décembre 2025 », « 11. Dezember 2025 », « 17 juil. 2026 »
_MONTH_NAME_RE = re.compile(r'\b(\d{1,2})\.?\s+([A-Za-zÀ-ÿ]+)\.?,?\s+(\d{4})\b')
# « August 5, 2026 » (ordre anglais)
_MONTH_FIRST_RE = re.compile(r'\b([A-Za-zÀ-ÿ]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b')


# Date présente dans l'URL : /2025/12/11/ (CasinoBeats, WordPress en général)
_URL_DATE_RE = re.compile(r'/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)')


def extract_date_from_url(url: str):
    """Récupère la date depuis le chemin de l'URL. Retourne datetime ou None."""
    m = _URL_DATE_RE.search(url or '')
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def is_too_old(text: str) -> bool:
    """True si le texte contient une année antérieure à MIN_YEAR.
    Sans année détectée → False (on garde, on ne peut pas juger)."""
    years = [int(y) for y in re.findall(r'\b(19\d{2}|20\d{2})\b', text)]
    if not years:
        return False
    return max(years) < MIN_YEAR


def extract_date(text: str):
    """Retourne un objet datetime ou None."""
    m = _DATE_RE.search(text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except Exception:
            pass
    # « 11 décembre 2025 » / « 17 juil. 2026 » / « 11. Dezember 2025 »
    m = _MONTH_NAME_RE.search(text)
    if m:
        month = _month_from_word(m.group(2))
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)))
            except Exception:
                pass

    # « August 5, 2026 » (ordre anglais)
    m = _MONTH_FIRST_RE.search(text)
    if m:
        month = _month_from_word(m.group(1))
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(2)))
            except Exception:
                pass
    return None


# ── Scraping ───────────────────────────────────────────────────────────────────

def fetch_rss(src: dict) -> dict:
    """Récupère un ou plusieurs flux RSS/Atom et filtre les entrées par mots-clés.
    `feeds` (liste) est prioritaire sur `url` : les entrées sont agrégées et dédoublonnées.
    """
    kw_set     = src.get('kw_set', 'institutional')
    feed_urls  = src.get('feeds') or [src['url']]
    matches    = []
    seen_links: set = set()
    last_error = ''
    ok_count   = 0

    for feed_url in feed_urls:
        try:
            resp = requests.get(feed_url, headers=HEADERS,
                                timeout=REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'xml')
            ok_count += 1
        except requests.exceptions.Timeout:
            last_error = f'Délai dépassé ({REQUEST_TIMEOUT}s)'
            continue
        except requests.exceptions.ConnectionError as e:
            last_error = f'Connexion impossible : {str(e)[:60]}'
            continue
        except requests.exceptions.HTTPError as e:
            last_error = f'HTTP {e.response.status_code}'
            continue
        except Exception as e:
            last_error = str(e)[:80]
            continue

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
            # Un même article peut figurer dans plusieurs flux RTBF
            if link in seen_links:
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

            # Filtre année : rejeter les entrées antérieures à MIN_YEAR
            if date_str and is_too_old(date_str):
                continue

            seen_links.add(link)
            matches.append({
                'text': title,
                'url': link,
                'keywords': kws,
                'context': desc[:220] if desc else '',
                'date': date_str,
            })

            if len(matches) >= MAX_MATCHES_PER_SOURCE:
                break

        if len(matches) >= MAX_MATCHES_PER_SOURCE:
            break

    # Erreur uniquement si AUCUN flux n'a répondu
    if ok_count == 0 and last_error:
        return {'status': 'error', 'message': last_error}
    return {'status': 'ok', 'matches': matches}


def fetch_with_browser(src: dict) -> dict:
    """Scrape une page JS-rendue via Playwright (Chromium headless).
    Supporte search_terms : 1 navigateur, N navigations successives.
    """
    _INPUT_SEL = (
        'input[type="search"], input[type="text"], '
        'input[name*="search" i], input[name*="zoek" i], '
        'input[name*="query" i], input[name*="Search" i], '
        'input[id*="search" i], input[placeholder*="recherch" i]'
    )
    _SUBMIT_SEL = (
        'button[type="submit"], input[type="submit"], '
        'button:has-text("Soumettre"), button:has-text("Rechercher"), '
        'button:has-text("Search"), button:has-text("Zoeken")'
    )

    kw_set    = src.get('kw_set', 'institutional')
    wait_sel  = src.get('wait_selector')
    eval_js   = src.get('eval_extract')
    base_url  = src['url']
    no_kw     = src.get('no_kw_filter', False)
    req_term  = src.get('require_term', False)   # le terme doit figurer dans le résultat
    pw_timeout = src.get('playwright_timeout', 45_000)
    fill_js   = src.get('fill_js')      # JS(term) → remplit le champ, retourne bool
    submit_js = src.get('submit_js')    # JS() → soumet le formulaire

    # Termes à parcourir (search_terms prioritaire, sinon search_query legacy, sinon [None])
    terms = (src.get('search_terms')
             or ([src['search_query']] if src.get('search_query') else [None]))

    all_matches: list = []
    seen_urls:   set  = set()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                )
            )

            for term in terms:
                # ── URL pour ce terme ──────────────────────────────────────
                is_template = term and '{term}' in base_url
                nav_url = base_url.replace('{term}', quote_plus(term)) if is_template else base_url

                page.goto(nav_url, wait_until='domcontentloaded', timeout=pw_timeout)
                page.wait_for_timeout(400)

                # ── Remplissage via JS dédié (formulaires POST complexes) ──
                inp = None
                if term and fill_js:
                    try:
                        ok = page.evaluate(fill_js, term)
                        print(f'    [browser] {src["name"]}: fill_js "{term}" → {ok}', flush=True)
                        if ok:
                            if submit_js:
                                page.evaluate(submit_js)
                            else:
                                page.keyboard.press('Enter')
                            try:
                                page.wait_for_load_state('networkidle', timeout=15_000)
                            except Exception:
                                pass
                            page.wait_for_timeout(800)
                            print(f'    [browser] {src["name"]}: → {page.url}', flush=True)
                    except Exception as e:
                        print(f'    [browser] {src["name"]}: fill_js échec — {str(e)[:80]}', flush=True)

                # ── Remplissage formulaire générique ──────────────────────
                elif term and not is_template:
                    try:
                        page.wait_for_selector(_INPUT_SEL, timeout=4_000)
                        inp = page.locator(_INPUT_SEL).first
                        inp.click()
                        inp.fill('')
                        inp.type(term, delay=10)
                        print(f'    [browser] {src["name"]}: champ rempli "{term}"', flush=True)
                    except Exception as e:
                        print(f'    [browser] {src["name"]}: champ introuvable — {e}', flush=True)

                    submitted = False
                    try:
                        page.locator(_SUBMIT_SEL).first.click(timeout=2_500)
                        submitted = True
                    except Exception:
                        pass
                    if not submitted and inp:
                        try:
                            inp.press('Enter')
                            submitted = True
                        except Exception:
                            pass

                    try:
                        page.wait_for_load_state('networkidle', timeout=6_000)
                    except Exception:
                        pass
                    page.wait_for_timeout(600)

                # ── Attendre le sélecteur de résultats (chemin rapide) ────
                # Si le sélecteur apparaît, on n'attend pas plus longtemps.
                found = False
                if wait_sel:
                    try:
                        page.wait_for_selector(wait_sel, timeout=6_000)
                        found = True
                    except Exception:
                        pass

                if not found:
                    # Pas de sélecteur (ou non trouvé) : attendre la fin des spinners
                    try:
                        page.wait_for_function(
                            """() => !document.body.innerText.includes('Chargement') &&
                                     !document.body.innerText.includes('Loading') &&
                                     !document.body.innerText.includes('laden')""",
                            timeout=4_000
                        )
                    except Exception:
                        pass
                    page.wait_for_timeout(500)

                # ── Extraction ciblée via evaluate() ─────────────────────
                if eval_js:
                    # Le terme est transmis au JS ; les snippets `() => …` l'ignorent
                    items = page.evaluate(eval_js, term)
                    for item in items:
                        href = item.get('href', '').strip()
                        text = item.get('text', '').strip()
                        if not href or not text:
                            continue
                        full_url = href if href.startswith('http') else urljoin(nav_url, href)
                        ckey = canon_url(full_url)
                        if ckey in seen_urls:
                            continue

                        tl = text.lower()
                        if any(b in tl for b in _BOILERPLATE):
                            continue
                        if is_too_old(text):
                            continue

                        kws = find_keywords(text, kw_set)
                        if not kws:
                            if not no_kw:
                                continue
                            kws = [term] if term else ['résultat']
                        seen_urls.add(ckey)
                        date_obj = extract_date(text) or extract_date_from_url(full_url)
                        date_str = date_obj.strftime('%d/%m/%Y') if date_obj else ''
                        all_matches.append({
                            'text': text[:300],
                            'url': full_url,
                            'keywords': kws,
                            'context': '',
                            'date': date_str,
                        })
                        if len(all_matches) >= MAX_MATCHES_PER_SOURCE:
                            break

                else:
                    # ── Extraction HTML générique (fallback) ──────────────
                    html = page.content()
                    print(f'    [browser] {src["name"]}: ~{html.count("<a ")} <a> (terme: {term})', flush=True)

                    # Debug dump pour le premier terme uniquement
                    if term == terms[0]:
                        debug_path = Path(f'data/debug_{src["id"]}.html')
                        debug_path.parent.mkdir(exist_ok=True)
                        debug_path.write_text(html[:50_000], encoding='utf-8')

                    soup = BeautifulSoup(html, 'lxml')
                    for tag in soup.find_all(['nav', 'footer', 'script', 'style', 'header']):
                        tag.decompose()

                    for a in soup.find_all('a', href=True):
                        text = a.get_text(' ', strip=True)
                        if not text or len(text) < 4 or len(text) > 400:
                            continue
                        href = a['href'].strip()
                        if not href or href.startswith('javascript') or href in ('#', ''):
                            continue
                        full_url = href if href.startswith('http') else urljoin(nav_url, href)
                        if canon_url(full_url) in seen_urls:
                            continue

                        block = a
                        for _ in range(3):
                            parent = block.find_parent(['article', 'li', 'tr', 'div', 'section', 'p'])
                            if parent:
                                block = parent
                                if len(block.get_text(' ', strip=True)) > 80:
                                    break
                        ctx = block.get_text(' ', strip=True)[:400]

                        # Filtres boilerplate + année (inconditionnels)
                        tl = text.lower()
                        if any(b in tl for b in _BOILERPLATE):
                            continue
                        if is_too_old(text + ' ' + ctx):
                            continue
                        # Exiger le terme recherché : élimine menus et navigation
                        if req_term and term and normalize(term) not in normalize(text + ' ' + ctx):
                            continue

                        kws = find_keywords(text + ' ' + ctx, kw_set)
                        if not kws:
                            if not no_kw or len(text) < 20:
                                continue
                            kws = [term] if term else ['résultat']

                        seen_urls.add(canon_url(full_url))
                        date_obj = (extract_date(ctx) or extract_date(text)
                                    or extract_date_from_url(full_url))
                        date_str = date_obj.strftime('%d/%m/%Y') if date_obj else ''
                        display_text = text if len(text) >= 15 else ctx[:120]

                        all_matches.append({
                            'text': display_text,
                            'url': full_url,
                            'keywords': kws,
                            'context': ctx if ctx != display_text else '',
                            'date': date_str,
                        })
                        if len(all_matches) >= MAX_MATCHES_PER_SOURCE:
                            break

                if len(all_matches) >= MAX_MATCHES_PER_SOURCE:
                    break

            browser.close()

        print(f'    [browser] {src["name"]}: {len(all_matches)} correspondance(s) au total', flush=True)
        return {'status': 'ok', 'matches': all_matches}

    except PWTimeout:
        return {'status': 'error', 'message': f'Délai dépassé (Playwright {pw_timeout // 1000}s)'}
    except Exception as e:
        return {'status': 'error', 'message': f'Playwright : {str(e)[:80]}'}


def fetch_source(src: dict) -> dict:
    if src.get('type') == 'rss':
        return fetch_rss(src)
    if src.get('js'):
        return fetch_with_browser(src)

    kw_set   = src.get('kw_set', 'institutional')
    base_url = src['url']
    terms    = src.get('search_terms') or [None]
    no_kw    = src.get('no_kw_filter', False)

    all_matches: list = []
    seen_urls:   set  = set()
    last_error:  str  = ''

    for term in terms:
        url = base_url.replace('{term}', quote_plus(term)) if (term and '{term}' in base_url) else base_url
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or 'utf-8'

            soup = BeautifulSoup(resp.text, 'lxml')
            for tag in soup.find_all(['nav', 'footer', 'script', 'style', 'header']):
                tag.decompose()

            for a in soup.find_all('a', href=True):
                text = a.get_text(' ', strip=True)
                if not text or len(text) < 10 or len(text) > 350:
                    continue
                href = a['href'].strip()
                if not href or href.startswith('javascript') or href in ('#', ''):
                    continue
                full_url = href if href.startswith('http') else urljoin(url, href)
                if canon_url(full_url) in seen_urls:
                    continue

                block = a.find_parent(['article', 'li', 'tr', 'p', 'div', 'section'])
                ctx = block.get_text(' ', strip=True)[:280] if block else text

                # Filtres boilerplate + année (inconditionnels)
                tl = text.lower()
                if any(b in tl for b in _BOILERPLATE):
                    continue
                if is_too_old(text + ' ' + ctx):
                    continue

                kws = find_keywords(text + ' ' + ctx, kw_set)
                if not kws:
                    if not no_kw or len(text) < 20:
                        continue
                    kws = [term] if term else ['résultat']

                seen_urls.add(canon_url(full_url))
                date_obj = (extract_date(ctx) or extract_date(text)
                            or extract_date_from_url(full_url))
                date_str = date_obj.strftime('%d/%m/%Y') if date_obj else ''

                all_matches.append({
                    'text': text,
                    'url': full_url,
                    'keywords': kws,
                    'context': ctx if ctx != text else '',
                    'date': date_str,
                })
                if len(all_matches) >= MAX_MATCHES_PER_SOURCE:
                    break

        except requests.exceptions.Timeout:
            last_error = f'Délai dépassé ({REQUEST_TIMEOUT}s)'
        except requests.exceptions.ConnectionError as e:
            last_error = f'Connexion impossible : {str(e)[:60]}'
        except requests.exceptions.HTTPError as e:
            last_error = f'HTTP {e.response.status_code}'
        except Exception as e:
            last_error = str(e)[:80]

        if len(all_matches) >= MAX_MATCHES_PER_SOURCE:
            break
        if len(terms) > 1:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    if not all_matches and last_error:
        return {'status': 'error', 'message': last_error}
    return {'status': 'ok', 'matches': all_matches}


# ── Génération HTML ────────────────────────────────────────────────────────────

def slug(s: str) -> str:
    """Identifiant technique stable pour un nom de groupe."""
    return re.sub(r'[^a-z0-9]+', '-', normalize(s)).strip('-')


def year_of(date_str: str) -> str:
    """Année d'une date jj/mm/aaaa, ou 'na' si absente/illisible."""
    if date_str and len(date_str) >= 4 and date_str[-4:].isdigit():
        return date_str[-4:]
    return 'na'


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
    _seen_titles: set = set()
    for src in SOURCES:
        result = results.get(src['id'], {})
        for m in result.get('matches', []):
            # Dédoublonnage : même dossier publié sous plusieurs URLs
            key = (src['id'], normalize(m.get('text', ''))[:90])
            if key in _seen_titles:
                continue
            _seen_titles.add(key)
            all_alerts.append({
                'source_name': src['name'],
                'source_color': src['color'],
                'group_slug': slug(src.get('group', '')),
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
            js_url   = esc(json.dumps(a['url']))
            js_title = esc(json.dumps(a['text']))
            js_date  = esc(json.dumps(a.get('date', '')))
            js_src   = esc(json.dumps(a['source_name']))
            rows += f'''<div class="alert-row" id="{row_id}" data-url="{esc(a['url'])}" data-grp="{a.get('group_slug','')}" data-year="{year_of(a.get('date',''))}">
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
        gslug = slug(grp)
        if grp != current_group:
            current_group = grp
            cards_html += (f'<div class="group-label" data-grp="{gslug}">'
                           f'{esc(grp)}</div>')

        result = results.get(src['id'], {'status': 'error', 'message': 'Non exécuté'})
        status = result.get('status', 'error')
        matches = result.get('matches', [])

        if status == 'ok':
            dot = 'ok'
            n = len(matches)
            badge = (f'<span class="badge badge-match" data-count>🔴 {n} alerte{"s" if n > 1 else ""}</span>'
                     if matches else '<span class="badge badge-none" data-count>0 alerte</span>')
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
                items += (f'<div class="result" data-year="{year_of(m.get("date",""))}">'
                          f'{date_html}'
                          f'<a href="{esc(m["url"])}" target="_blank">{esc(m["text"])}</a>'
                          f'{ctx_html}'
                          f'<div class="tag-row">{tags}</div>'
                          f'</div>')
            body = items

        cards_html += f'''<div class="card" data-grp="{gslug}" style="border-left-color:{src["color"]}">
  <div class="card-head">
    <div>
      <div class="card-title"><span class="dot {dot}"></span> {esc(src["name"])}</div>
      <a class="card-url" href="{esc(src["url"])}" target="_blank">{esc(src["url"])}</a>
    </div>
    {badge}
  </div>
  <div class="card-body">{body}</div>
</div>'''

    # ── Boutons de filtrage par catégorie ─────────────────────────────────────
    _groups: list = []
    for src in SOURCES:
        g = src.get('group', '')
        if g and g not in _groups:
            _groups.append(g)
    grp_buttons = ''.join(
        f'<button class="grp-btn" id="gb-{slug(g)}" data-grp="{slug(g)}" '
        f'title="Masquer / afficher cette catégorie" '
        f'onclick="toggleGroup(\'{slug(g)}\')">{esc(g)}</button>'
        for g in _groups
    )

    # ── Boutons de filtrage par année (MIN_YEAR → année en cours) ─────────────
    _years = [str(y) for y in range(MIN_YEAR, datetime.now(timezone.utc).year + 1)]
    year_buttons = ''.join(
        f'<button class="grp-btn yr-btn" id="yb-{y}" data-year="{y}" '
        f'title="Masquer / afficher les résultats de {y}" '
        f'onclick="toggleYear(\'{y}\')">{y}</button>'
        for y in _years
    )

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
.filter-box{margin-top:10px;background:rgba(0,0,0,.22);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:10px 12px 11px}
.filter-hint{font-size:11px;font-weight:600;color:rgba(255,255,255,.65);margin-bottom:8px}
.grp-row{margin-top:7px;display:flex;flex-wrap:wrap;gap:7px;align-items:center}
.grp-row:first-of-type{margin-top:0}
.grp-row-label{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.5);min-width:78px}
.yr-btn{font-variant-numeric:tabular-nums}
.reset-btn{margin-left:auto;background:rgba(255,255,255,.08);border-style:dashed}
.grp-btn{font-size:12px;font-weight:700;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.22);color:#fff;border-radius:14px;padding:6px 15px;cursor:pointer;transition:all .15s;white-space:nowrap;line-height:1.3}
.grp-btn:hover{background:rgba(255,255,255,.28)}
.grp-btn.off{background:transparent;color:rgba(255,255,255,.35);border-color:rgba(255,255,255,.15);text-decoration:line-through}
/* Carte repliee : on garde l'en-tete (titre + badge), on cache le contenu */
.card.grp-hidden .card-body{display:none}
.card.grp-hidden{opacity:.5}
.card.grp-hidden .card-url{display:none}
.group-label.grp-hidden{opacity:.4}
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
.btn-unread{flex-shrink:0;font-size:10px;font-weight:600;background:rgba(52,152,219,.12);color:#2980b9;border:none;border-radius:4px;padding:3px 9px;cursor:pointer;white-space:nowrap;margin-top:2px}
[data-theme=dark] .btn-unread{color:#58a6ff;background:rgba(88,166,255,.12)}
.btn-unread:hover{opacity:.75}
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
const KEY_GRP   = 'bgw_groups_off';
const KEY_YEAR  = 'bgw_years_off';

function loadSet(key){
  try { return new Set(JSON.parse(localStorage.getItem(key) || '[]')); }
  catch(e){ return new Set(); }
}
const groupsOff = () => loadSet(KEY_GRP);
const yearsOff  = () => loadSet(KEY_YEAR);

// ── Filtrage par categorie ────────────────────────────────────────────────────
function toggleGroup(g){
  const off = groupsOff();
  off.has(g) ? off.delete(g) : off.add(g);
  localStorage.setItem(KEY_GRP, JSON.stringify([...off]));
  applyFilters();
}

// ── Filtrage par annee ────────────────────────────────────────────────────────
function toggleYear(y){
  const off = yearsOff();
  off.has(y) ? off.delete(y) : off.add(y);
  localStorage.setItem(KEY_YEAR, JSON.stringify([...off]));
  applyFilters();
}

function resetFilters(){
  localStorage.removeItem(KEY_GRP);
  localStorage.removeItem(KEY_YEAR);
  applyFilters();
}

// ── Application de tous les filtres ───────────────────────────────────────────
function applyFilters(){
  const gOff = groupsOff();
  const yOff = yearsOff();
  const read = new Set((JSON.parse(localStorage.getItem(KEY_READ)||'[]')).map(i=>i.url));

  // Etat visuel des boutons
  document.querySelectorAll('.grp-btn[data-grp]').forEach(b=>{
    b.classList.toggle('off', gOff.has(b.dataset.grp));
  });
  document.querySelectorAll('.grp-btn[data-year]').forEach(b=>{
    b.classList.toggle('off', yOff.has(b.dataset.year));
  });

  // Cartes : repliees si categorie masquee (le titre reste visible)
  document.querySelectorAll('.card[data-grp], .group-label[data-grp]').forEach(el=>{
    el.classList.toggle('grp-hidden', gOff.has(el.dataset.grp));
  });

  // Resultats dans les cartes individuelles : filtres par annee
  document.querySelectorAll('.card[data-grp]').forEach(card=>{
    let shown = 0;
    const items = card.querySelectorAll('.result[data-year]');
    items.forEach(r=>{
      const hide = yOff.has(r.dataset.year);
      r.style.display = hide ? 'none' : '';
      if(!hide) shown++;
    });
    const badge = card.querySelector('.badge[data-count]');
    if(badge && items.length){
      badge.textContent = shown ? '🔴 ' + shown + ' alerte' + (shown > 1 ? 's' : '')
                                : '0 alerte';
      badge.className = 'badge ' + (shown ? 'badge-match' : 'badge-none');
      badge.setAttribute('data-count','');
    }
  });

  // Resume : masque si lu, categorie masquee ou annee masquee
  let visible = 0;
  document.querySelectorAll('.alert-row[data-url]').forEach(row=>{
    const hide = read.has(row.dataset.url)
              || gOff.has(row.dataset.grp)
              || yOff.has(row.dataset.year);
    row.style.display = hide ? 'none' : '';
    if(!hide) visible++;
  });
  const sb = document.getElementById('summary-badge');
  if(sb) sb.textContent = visible + ' alerte' + (visible !== 1 ? 's' : '');
}

// Alias conserve pour les appels existants
const refreshRows = applyFilters;
const applyGroups = applyFilters;

// ── Utilitaire HTML-escape (pour renderArchive) ────────────────────────────────
function eh(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

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
    // On stocke uniquement la date de publication (pas la date du clic)
    items.unshift({url, title, date, src});
    localStorage.setItem(KEY_READ, JSON.stringify(items.slice(0,300)));
  }
  const row = document.getElementById(rowId);
  if(row){
    row.style.opacity='0'; row.style.transition='opacity .3s';
    setTimeout(()=>{ row.style.opacity='1'; refreshRows(); }, 300);
  } else { refreshRows(); }
  renderArchive();
}

// ── Remettre comme non lu ──────────────────────────────────────────────────────
function unmarkRead(url){
  let items = JSON.parse(localStorage.getItem(KEY_READ)||'[]');
  items = items.filter(i=>i.url!==url);
  localStorage.setItem(KEY_READ, JSON.stringify(items));
  refreshRows();   // respecte aussi le filtre par categorie
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
    <div class="archive-row" data-url="${eh(i.url)}">
      <span class="archive-date">${eh(i.date||'—')}</span>
      <span class="archive-src" style="color:#888">${eh(i.src)}</span>
      <a href="${eh(i.url)}" target="_blank">${eh(i.title)}</a>
      <button class="btn-unread" onclick="unmarkRead(this.closest('.archive-row').dataset.url)">↩ Non lu</button>
    </div>`).join('');
}

// ── Section À propos ──────────────────────────────────────────────────────────
function toggleAbout(){
  const btn  = document.getElementById('about-btn');
  const body = document.getElementById('about-body');
  btn.classList.toggle('open');
  body.classList.toggle('open');
}

// ── Init : cacher les alertes déjà lues au chargement ────────────────────────
document.addEventListener('DOMContentLoaded', function(){
  applyTheme(localStorage.getItem(KEY_THEME)||'light');
  applyFilters();
  renderArchive();
});
"""

    return f"""<!DOCTYPE html>
<html lang="fr" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>News feed Ladbrokes Robot</title>
<style>{css}</style>
</head>
<body>
<header>
  <button id="theme-btn" onclick="toggleTheme()">🌙 Sombre</button>
  <h1>🇧🇪 News feed Ladbrokes Robot</h1>
  <div id="ts">Dernière vérification : {esc(run_time)} — {total_alerts} alerte{"s" if total_alerts != 1 else ""} au total{new_badge_header}</div>
  <div class="kw-row">{kw_chips}</div>
  <div class="filter-box">
    <div class="filter-hint">🎛️ Filtres — cliquez pour masquer, recliquez pour réafficher</div>
    <div class="grp-row"><span class="grp-row-label">Catégories</span>{grp_buttons}</div>
    <div class="grp-row"><span class="grp-row-label">Années</span>{year_buttons}
      <button class="grp-btn reset-btn" onclick="resetFilters()" title="Tout réafficher">↺ Tout afficher</button>
    </div>
  </div>
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
    <p>Chaque site est interrogé <strong>séparément pour chacun des 4 termes</strong> : <em>ladbrokes, entain, bwin, jeux de hasard</em>. Les résultats antérieurs à <strong>2024</strong> sont écartés.</p>
    <p><strong>Sources institutionnelles</strong> : Cour Constitutionnelle, Gaming Commission, Moniteur Belge, Autorité belge de la Concurrence, SPF Justice.</p>
    <p><strong>Presse belge francophone</strong> : RTBF (5 flux + archives), Le Soir, La Libre, DH, RTL Info, Sud Info, 7sur7.</p>
    <p><strong>Presse spécialisée &amp; Europe</strong> : CasinoBeats, EUR-Lex.</p>
    <p>Les boutons <strong>Catégories</strong> en haut de page replient les cartes d'une catégorie et masquent ses alertes dans le résumé. Le bouton <strong>✓ Lu</strong> déplace une alerte dans « Alertes déjà lues ». Ces réglages sont stockés localement dans votre navigateur.</p>
    <p>Infrastructure : <strong>GitHub Actions</strong> exécute le script Python · <strong>GitHub Pages</strong> héberge cette page · LANCELLE 2026.</p>
  </div>
</div>

</div>
<footer>Veille Jeux d'Argent Belgique · LANCELLE 2026 · Généré automatiquement par GitHub Actions</footer>
<script>{js}</script>
</body>
</html>"""


# ── Issue GitHub ───────────────────────────────────────────────────────────────

def md_line(m: dict) -> str:
    """Une ligne Markdown propre pour une correspondance."""
    # Le texte extrait contient des retours à la ligne qui cassent la liste Markdown
    txt = re.sub(r'\s+', ' ', m.get('text', '')).strip()[:180]
    txt = txt.replace('[', '(').replace(']', ')')     # évite de casser le lien
    kws = ', '.join(f'`{kw}`' for kw in m.get('keywords', []))
    date = m.get('date', '')
    prefix = f'**{date}** — ' if date else '_(sans date)_ — '
    return f'- {prefix}[{txt}]({m["url"]}) — {kws}'


def sorted_by_date(matches: list) -> list:
    """Plus récent en premier ; les sans-date à la fin."""
    return sorted(matches, key=lambda m: _parse_date(m.get('date', '')), reverse=True)


def create_github_issue(new_matches_by_src: dict, run_time: str) -> None:
    token = os.environ.get('GITHUB_TOKEN')
    repo  = os.environ.get('GITHUB_REPOSITORY')

    if not token or not repo:
        print('⚠️  GITHUB_TOKEN / GITHUB_REPOSITORY absent — issue non créée')
        return

    total = sum(len(v) for v in new_matches_by_src.values())

    lines = [f'Vérification du **{run_time}** — {total} nouvelle'
             f'{"s" if total != 1 else ""} correspondance'
             f'{"s" if total != 1 else ""}\n']

    # ── Vue chronologique globale, toutes sources confondues ──────────────────
    flat = []
    for src_name, matches in new_matches_by_src.items():
        for m in matches:
            flat.append({**m, '_src': src_name})
    flat = sorted_by_date(flat)

    lines.append('\n## Par date (plus récent en premier)\n')
    for m in flat:
        lines.append(md_line(m) + f' — _{m["_src"]}_')

    # ── Détail par source, chaque source triée par date ───────────────────────
    lines.append('\n---\n\n## Par source\n')
    for src_name, matches in new_matches_by_src.items():
        lines.append(f'\n### {src_name}\n')
        for m in sorted_by_date(matches):
            lines.append(md_line(m))
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

    t0 = time.time()

    def _keep(m: dict) -> bool:
        """Garde-fou final : rejeter tout ce qui est daté avant MIN_YEAR."""
        d = m.get('date', '')
        if d:
            try:
                if int(d.split('/')[-1]) < MIN_YEAR:
                    return False
            except Exception:
                pass
        return not is_too_old(m.get('text', '') + ' ' + m.get('context', ''))

    def _run(src):
        try:
            return src, fetch_source(src)
        except Exception as e:
            return src, {'status': 'error', 'message': str(e)[:80]}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for src, result in pool.map(_run, SOURCES):
            if result.get('status') == 'ok':
                result['matches'] = [m for m in result.get('matches', []) if _keep(m)]
            results[src['id']] = result
            if result['status'] == 'ok':
                n = len(result['matches'])
                print(f'  → {src["name"]} … ✅ {n} correspondance{"s" if n != 1 else ""}', flush=True)
                new_for_src = []
                for m in result['matches']:
                    current_urls.add(m['url'])
                    if m['url'] not in prev_urls:
                        new_for_src.append(m)
                if new_for_src:
                    new_matches_by_src[src['name']] = new_for_src
            else:
                print(f'  → {src["name"]} … ❌ {result["message"]}', flush=True)

    print(f'\n⏱️  Scraping terminé en {time.time() - t0:.1f}s')

    run_time = datetime.now(timezone.utc).strftime('%d/%m/%Y à %H:%M UTC')
    new_count = sum(len(v) for v in new_matches_by_src.values())

    print(f'\n📊 Résumé : {new_count} nouvelle{"s" if new_count != 1 else ""} correspondance{"s" if new_count != 1 else ""}')

    # Comptage total pour le status
    total_alerts = sum(
        len(r.get('matches', []))
        for r in results.values()
        if r.get('status') == 'ok'
    )

    html = generate_html(results, run_time, new_count)
    Path('index.html').write_text(html, encoding='utf-8')
    print('✅ index.html généré')

    # ── status.md ──────────────────────────────────────────────────────────────
    status_lines = [
        '# Statut — Veille Jeux d\'Argent Belgique',
        '',
        f'| Champ | Valeur |',
        f'|---|---|',
        f'| Dernière vérification | {run_time} |',
        f'| Alertes totales | {total_alerts} |',
        f'| Nouvelles depuis la veille | {new_count} |',
        f'| Sources interrogées | {len(SOURCES)} |',
        '',
    ]
    if new_count:
        status_lines += ['## Nouvelles alertes', '']
        for src_name, matches in new_matches_by_src.items():
            status_lines.append(f'### {src_name}')
            status_lines.append('')
            for m in sorted_by_date(matches):
                status_lines.append(md_line(m))
            status_lines.append('')
    else:
        status_lines.append('*Aucune nouvelle alerte depuis la dernière vérification.*')
        status_lines.append('')

    status_lines += [
        '---',
        f'*Généré automatiquement par GitHub Actions · LANCELLE 2026*',
    ]
    Path('status.md').write_text('\n'.join(status_lines), encoding='utf-8')
    print('✅ status.md généré')

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
