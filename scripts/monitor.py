#!/usr/bin/env python3
"""
Veille Jeux d'Argent Belgique
Scrape les sources, cherche les mots-clés, génère index.html,
crée une issue GitHub en cas de nouvelles correspondances.
"""

import json
import os
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

KEYWORDS = [
    'entaingroup', 'ladbrokes', 'controle', 'contrôle',
    'sanctions', 'agences', 'licences',
]

SOURCES = [
    # ── Institutionnel belge ───────────────────────────────────────────────────
    {
        'id': 'cour', 'group': 'Institutionnel belge',
        'name': 'Cour Constitutionnelle', 'color': '#1a5276',
        'url': 'https://fr.const-court.be/communication/communiques-de-presse',
    },
    {
        'id': 'chambre', 'group': 'Institutionnel belge',
        'name': 'La Chambre des Représentants', 'color': '#1a6640',
        'url': 'https://www.lachambre.be/kvvcr/showpage.cfm?section=none&language=fr&cfm=news.cfm',
    },
    {
        'id': 'senat', 'group': 'Institutionnel belge',
        'name': 'Sénat de Belgique', 'color': '#1a6640',
        'url': 'https://www.senate.be/www/?MIval=/index_senate&lang=fr',
    },
    {
        'id': 'cjh', 'group': 'Institutionnel belge',
        'name': 'Gaming Commission (CJH)', 'color': '#7d3c98',
        'url': 'https://www.gamingcommission.be/fr/news',
    },
    {
        'id': 'moniteur', 'group': 'Institutionnel belge',
        'name': 'Moniteur Belge', 'color': '#2c3e50',
        'url': 'https://www.ejustice.just.fgov.be/cgi/summary.pl?language=fr',
    },
    {
        'id': 'consetat', 'group': 'Institutionnel belge',
        'name': "Conseil d'État", 'color': '#2c3e50',
        'url': 'https://www.raadvst-consetat.be/dbx/fr/actualites',
    },
    {
        'id': 'abc', 'group': 'Institutionnel belge',
        'name': 'Autorité belge de la Concurrence', 'color': '#2c3e50',
        'url': 'https://www.abc-bma.be/fr/news',
    },
    {
        'id': 'spfjust', 'group': 'Institutionnel belge',
        'name': 'SPF Justice', 'color': '#2c3e50',
        'url': 'https://justice.belgium.be/fr/news',
    },

    # ── Presse belge francophone ───────────────────────────────────────────────
    {
        'id': 'jeuarg', 'group': 'Presse belge francophone',
        'name': 'Jeu-Argent.be', 'color': '#e67e22',
        'url': 'https://www.jeu-argent.be',
    },
    {
        'id': 'medor', 'group': 'Presse belge francophone',
        'name': 'Médor (investigation)', 'color': '#c0392b',
        'url': 'https://medor.coop/nos-coups/',
    },
    {
        'id': 'soir', 'group': 'Presse belge francophone',
        'name': 'Le Soir', 'color': '#1565c0',
        'url': 'https://www.lesoir.be/recherche?q=jeux+hasard+ladbrokes+entain',
    },
    {
        'id': 'rtbf', 'group': 'Presse belge francophone',
        'name': 'RTBF Info', 'color': '#e53935',
        'url': 'https://www.rtbf.be/recherche?q=jeux+hasard+ladbrokes+entain',
    },
    {
        'id': 'lalibre', 'group': 'Presse belge francophone',
        'name': 'La Libre Belgique', 'color': '#0d47a1',
        'url': 'https://www.lalibre.be/recherche?q=jeux+hasard+ladbrokes+entain',
    },
    {
        'id': 'dhnet', 'group': 'Presse belge francophone',
        'name': 'La Dernière Heure', 'color': '#b71c1c',
        'url': 'https://www.dhnet.be/recherche?q=jeux+hasard+ladbrokes+entain',
    },
    {
        'id': 'rtlinfo', 'group': 'Presse belge francophone',
        'name': 'RTL Info', 'color': '#ff6f00',
        'url': 'https://www.rtl.be/info/recherche?q=jeux+hasard+ladbrokes',
    },
    {
        'id': 'levif', 'group': 'Presse belge francophone',
        'name': 'Le Vif', 'color': '#6a1b9a',
        'url': 'https://www.levif.be/recherche/?q=jeux+hasard+ladbrokes',
    },
    {
        'id': 'sudinfo', 'group': 'Presse belge francophone',
        'name': 'Sud Info', 'color': '#e65100',
        'url': 'https://www.sudinfo.be/recherche?q=jeux+hasard+ladbrokes',
    },

    # ── Opérateurs ────────────────────────────────────────────────────────────
    {
        'id': 'entain', 'group': 'Opérateurs',
        'name': 'Entain Group', 'color': '#c0392b',
        'url': 'https://www.entaingroup.com/news-and-insights/',
    },

    # ── Presse spécialisée & Europe ───────────────────────────────────────────
    {
        'id': 'sbc', 'group': 'Presse spécialisée & Europe',
        'name': 'SBC News — Belgique', 'color': '#2471a3',
        'url': 'https://sbcnews.co.uk/tag/belgium/',
    },
    {
        'id': 'igaming', 'group': 'Presse spécialisée & Europe',
        'name': 'iGaming Business', 'color': '#2471a3',
        'url': 'https://igamingbusiness.com/?s=belgium',
    },
    {
        'id': 'casinobeats', 'group': 'Presse spécialisée & Europe',
        'name': 'CasinoBeats', 'color': '#2471a3',
        'url': 'https://casinobeats.com/?s=belgium',
    },
    {
        'id': 'egba', 'group': 'Presse spécialisée & Europe',
        'name': 'EGBA (European Gaming & Betting Assoc.)', 'color': '#1565c0',
        'url': 'https://www.egba.eu/news/',
    },
    {
        'id': 'eurlex', 'group': 'Presse spécialisée & Europe',
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
DELAY_BETWEEN_REQUESTS = 1.5   # secondes


# ── Utilitaires ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Minuscules + suppression des accents."""
    text = text.lower()
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    return text


def find_keywords(text: str) -> list:
    n = normalize(text)
    found = []
    for kw in KEYWORDS:
        if normalize(kw) in n and kw not in found:
            found.append(kw)
    return found


# ── Scraping ───────────────────────────────────────────────────────────────────

def fetch_source(src: dict) -> dict:
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

            # Contexte : bloc parent le plus proche porteur de sens
            block = a.find_parent(['article', 'li', 'tr', 'p', 'div', 'section'])
            ctx = block.get_text(' ', strip=True)[:280] if block else text

            kws = find_keywords(text + ' ' + ctx)
            if not kws:
                continue

            matches.append({
                'text': text,
                'url': full_url,
                'keywords': kws,
                'context': ctx if ctx != text else '',
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


def generate_html(results: dict, run_time: str, new_count: int) -> str:
    # Comptage par mot-clé
    kw_counts = {kw: 0 for kw in KEYWORDS}
    for result in results.values():
        for m in result.get('matches', []):
            for kw in m.get('keywords', []):
                if kw in kw_counts:
                    kw_counts[kw] += 1

    total_alerts = sum(kw_counts.values())

    # Chips mots-clés
    kw_chips = ''
    for kw, cnt in kw_counts.items():
        if cnt:
            kw_chips += (f'<span class="kw-chip active">{esc(kw)} '
                         f'<span class="n">{cnt}</span></span>')
        else:
            kw_chips += f'<span class="kw-chip">{esc(kw)}</span>'

    # Cartes sources
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
            if matches:
                n = len(matches)
                badge = (f'<span class="badge badge-match">'
                         f'🔴 {n} alerte{"s" if n > 1 else ""}</span>')
            else:
                badge = '<span class="badge badge-none">0 alerte</span>'
        else:
            dot = 'err'
            badge = '<span class="badge badge-err">Erreur</span>'

        # Corps de la carte
        if status == 'error':
            body = f'<div class="err-msg">⚠️ {esc(result.get("message", "Erreur inconnue"))}</div>'
        elif not matches:
            body = '<div class="empty">Aucune correspondance pour ces mots-clés.</div>'
        else:
            items = ''
            for m in matches:
                tags = ''.join(f'<span class="tag">{esc(kw)}</span>'
                               for kw in m['keywords'])
                ctx_html = (f'<div class="ctx">{esc(m["context"][:220])}…</div>'
                            if m.get('context') else '')
                items += (f'<div class="result">'
                          f'<a href="{esc(m["url"])}" target="_blank">{esc(m["text"])}</a>'
                          f'{ctx_html}'
                          f'<div class="tag-row">{tags}</div>'
                          f'</div>')
            body = items

        new_badge = (f' — <strong style="color:#e74c3c">'
                     f'{new_count} nouvelle{"s" if new_count != 1 else ""} '
                     f'depuis la dernière vérification</strong>') if new_count else ''

        cards_html += f'''
<div class="card" style="border-left-color:{src["color"]}">
  <div class="card-head">
    <div>
      <div class="card-title"><span class="dot {dot}"></span> {esc(src["name"])}</div>
      <a class="card-url" href="{esc(src["url"])}" target="_blank">{esc(src["url"])}</a>
    </div>
    {badge}
  </div>
  <div class="card-body">{body}</div>
</div>'''

    css = """
:root{color-scheme:light}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#eef0f4;color:#1a1a2e;font-size:14px}
header{background:linear-gradient(135deg,#0d1b2a,#1b3a5c);color:#fff;padding:16px 20px 12px}
header h1{font-size:17px;font-weight:700;margin-bottom:4px}
#ts{font-size:11px;color:rgba(255,255,255,.55)}
.kw-row{margin-top:6px;display:flex;flex-wrap:wrap;gap:5px}
.kw-chip{font-size:10px;font-weight:600;background:rgba(255,255,255,.12);border-radius:4px;padding:2px 8px;color:rgba(255,255,255,.8)}
.kw-chip.active{background:rgba(231,76,60,.3)}
.kw-chip .n{display:inline-block;background:#e74c3c;color:#fff;border-radius:8px;padding:0 4px;margin-left:5px;font-size:9px}
.page{padding:14px;display:flex;flex-direction:column;gap:10px}
.group-label{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:#9aa;padding:6px 2px 2px;border-bottom:1px solid #dde0e8;margin-bottom:2px}
.card{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid #ccc;overflow:hidden}
.card-head{display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid #f2f3f5;gap:10px}
.card-title{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:#ccc;flex-shrink:0;display:inline-block}
.dot.ok{background:#2ecc71}.dot.err{background:#e67e22}
.card-url{font-size:10px;color:#b0b4bc;margin-top:1px;text-decoration:none;display:block}
.card-url:hover{color:#3498db}
.badge{font-size:10px;font-weight:700;padding:3px 10px;border-radius:10px;white-space:nowrap}
.badge-match{background:#fde8e8;color:#c0392b}
.badge-none{background:#eef0f4;color:#999}
.badge-err{background:#fff3e6;color:#d35400}
.card-body{padding:10px 14px 12px}
.result{padding:8px 0;border-bottom:1px solid #f5f6fa;line-height:1.45}
.result:last-child{border-bottom:none}
.result a{font-size:12.5px;font-weight:600;color:#1a5276;text-decoration:none;display:block}
.result a:hover{color:#2980b9;text-decoration:underline}
.ctx{font-size:11px;color:#888;margin-top:3px;line-height:1.4}
.tag-row{margin-top:4px;display:flex;gap:4px;flex-wrap:wrap}
.tag{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:1px 6px;border-radius:3px;background:#fff8dc;color:#7d5a00}
.empty{font-size:11.5px;color:#c0c4cc;padding:4px 0;font-style:italic}
.err-msg{font-size:11.5px;color:#d35400;padding:4px 0}
footer{text-align:center;font-size:10px;color:#bbb;padding:10px 16px 20px}
"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Veille — Jeux d'Argent Belgique</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>🇧🇪 Veille — Jeux d'Argent Belgique</h1>
  <div id="ts">Dernière vérification : {esc(run_time)} — {total_alerts} alerte{"s" if total_alerts != 1 else ""} au total{new_badge}</div>
  <div class="kw-row">{kw_chips}</div>
</header>
<div class="page">{cards_html}</div>
<footer>Veille Jeux d'Argent Belgique · LANCELLE 2026 · Généré automatiquement par GitHub Actions</footer>
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
            lines.append(f'- [{m["text"]}]({m["url"]}) — {kws}')

    total = sum(len(v) for v in new_matches_by_src.values())
    title = f'🔴 {total} nouvelle{"s" if total != 1 else ""} correspondance{"s" if total != 1 else ""} — {run_time}'

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

    # Charger les URLs déjà vues
    prev_urls: set = set()
    if data_path.exists():
        try:
            prev = json.loads(data_path.read_text(encoding='utf-8'))
            prev_urls = set(prev.get('urls', []))
        except Exception:
            pass

    print(f'🔍 Veille démarrée — {len(SOURCES)} sources, mots-clés : {", ".join(KEYWORDS)}\n')

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

    # Générer le dashboard HTML
    html = generate_html(results, run_time, new_count)
    Path('index.html').write_text(html, encoding='utf-8')
    print('✅ index.html généré')

    # Sauvegarder l'état pour la prochaine comparaison
    data_path.write_text(
        json.dumps({'urls': sorted(current_urls), 'last_run': run_time},
                   ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print('✅ data/last_results.json mis à jour')

    # Créer une issue si nouvelles correspondances
    if new_matches_by_src:
        print(f'\n🔔 {new_count} nouvelle(s) — création d\'une issue GitHub…')
        create_github_issue(new_matches_by_src, run_time)
    else:
        print('\nℹ️  Aucune nouvelle correspondance — pas d\'issue créée')


if __name__ == '__main__':
    main()
