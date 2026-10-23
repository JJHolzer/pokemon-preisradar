#!/usr/bin/env python3
"""Täglicher Abruf für den Preisradar.

1. Lädt den Cardmarket Price Guide + Produktlisten (Pokémon, idGame 6).
2. Aktualisiert die TCGdex-Zuordnung (deutsche Namen, Nummern, Bilder) inkrementell.
3. Schreibt den Preisverlauf je Produkt fort (state/history/<shard>.json).
4. Baut die Website nach site/ (Katalog, Verlauf, App).

Aufruf: python scripts/update.py --state state --site site
"""
import argparse, collections, concurrent.futures as cf, datetime as dt, json, os, re, shutil, sys, time, urllib.parse, urllib.request

CM = 'https://downloads.s3.cardmarket.com/productCatalog/'
TD = 'https://api.tcgdex.net/v2/de'
UA = {'User-Agent': 'pokemon-preisradar (github.com/JJHolzer/pokemon-preisradar)'}
SHARDS = 128
KEEP_DAYS = 400
MAX_TCGDEX_FETCH = 2500


def get_json(url, retries=4, timeout=90):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'{url}: {last}')


def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


# ---------------------------------------------------------------- TCGdex
def refresh_tcgdex(state, seed_path, today):
    path = os.path.join(state, 'tcgdex-de.json')
    db = load(path, None) or load(seed_path, {'cards': {}})
    cards = db['cards']
    try:
        listing = get_json(TD + '/cards')
    except Exception as e:
        print('TCGdex-Liste nicht erreichbar, nutze Bestand:', e)
        return cards
    ids = [c['id'] for c in listing]
    new = [i for i in ids if i not in cards]
    # Karten ohne Cardmarket-ID regelmäßig neu prüfen (Preise kommen oft erst nach Release)
    stale = sorted([i for i, c in cards.items() if not c[5]], key=lambda i: cards[i][7] or '')
    todo = (new + stale)[:MAX_TCGDEX_FETCH]
    print(f'TCGdex: {len(ids)} Karten, neu {len(new)}, ohne ID {len(stale)}, abgefragt {len(todo)}')

    def fetch(cid):
        try:
            d = get_json(TD + '/cards/' + urllib.parse.quote(urllib.parse.unquote(cid), safe=''), retries=2, timeout=30)
            cm = ((d.get('pricing') or {}).get('cardmarket') or {})
            s = d.get('set') or {}
            return cid, [d.get('localId', ''), d.get('name', ''), d.get('rarity') or '', s.get('id', ''), s.get('name', ''),
                         cm.get('idProduct'), d.get('image') or '', today]
        except Exception:
            return cid, None

    with cf.ThreadPoolExecutor(8) as ex:
        for cid, row in ex.map(fetch, todo):
            if row:
                cards[cid] = row
            elif cid in cards:
                cards[cid][7] = today
    dump(path, {'fields': ['localId', 'name', 'rarity', 'setId', 'setName', 'idProduct', 'image', 'checked'], 'cards': cards})
    return cards


def refresh_sets(state, today):
    path = os.path.join(state, 'tcgdex-sets.json')
    sets = load(path, {})
    if sets.get('_fetched', '') >= (dt.date.fromisoformat(today) - dt.timedelta(days=6)).isoformat():
        return sets
    try:
        for s in get_json(TD + '/sets'):
            cc = s.get('cardCount') or {}
            sets[s['id']] = {'name': s.get('name', ''), 'official': cc.get('official'), 'total': cc.get('total')}
        sets['_fetched'] = today
        dump(path, sets)
    except Exception as e:
        print('TCGdex-Sets nicht erreichbar:', e)
    return sets


# ---------------------------------------------------------------- Katalog
def clean_en(n):
    return re.sub(r'\s*\[.*?\]\s*', ' ', n).strip()


def build_catalog(pg, singles, nonsingles, cards, tsets):
    P = {x['idProduct']: x for x in pg['priceGuides']}
    rows = singles + nonsingles
    byid = {r['idProduct']: r for r in rows}
    de = {}
    for cid, c in cards.items():
        pid = c[5]
        if pid and pid in byid and pid not in de:
            de[pid] = (cid, c)
    # Serie je TCGdex-Set aus vorhandenen Bild-URLs
    serie = {}
    for cid, c in cards.items():
        m = re.match(r'https://assets\.tcgdex\.net/\w+/([^/]+)/([^/]+)/', c[6] or '')
        if m:
            serie[m.group(2)] = m.group(1)
    # Set-Namen und Kartenanzahl je Cardmarket-Expansion
    es = collections.defaultdict(collections.Counter)
    for pid, (cid, c) in de.items():
        es[byid[pid]['idExpansion']][(c[3], c[4])] += 1
    sets = {}
    for e, cnt in es.items():
        (sid, sname), _ = cnt.most_common(1)[0]
        ts = tsets.get(sid) or {}
        sets[e] = {'n': sname, 'id': sid, 'c': ts.get('official')}
    ns_by_exp = collections.defaultdict(list)
    for r in nonsingles:
        ns_by_exp[r['idExpansion']].append(r['name'])
    for e, names in ns_by_exp.items():
        if e in sets:
            continue
        b = [n for n in names if re.search(r' Booster$', n)]
        if b:
            nm = min(b, key=len)[:-8].strip()
        else:
            names = sorted(names, key=len)
            pre = os.path.commonprefix(names).rstrip(' :-–(')
            nm = pre if len(pre) >= 4 else re.split(r'[:\-–(]', names[0])[0].strip()
        sets[e] = {'n': nm, 'id': '', 'c': None}
    grp = collections.defaultdict(list)
    for r in singles:
        grp[(r['idExpansion'], r['name'])].append(r['idProduct'])
    ver = {}
    for ids in grp.values():
        if len(ids) > 1:
            for i, pid in enumerate(sorted(ids)):
                ver[pid] = i + 1

    def r2(v):
        return None if v is None else round(v, 2)

    out = []
    for r in rows:
        pid = r['idProduct']
        g = P.get(pid, {})
        trend, low = r2(g.get('trend')), r2(g.get('low'))
        if trend is None and low is None:
            continue
        cat = 'S' if r['categoryName'] == 'Pokémon Single' else r['categoryName'].replace('Pokémon ', '')
        t = de.get(pid)
        img = ''
        if t:
            cid, c = t
            m = re.match(r'https://assets\.tcgdex\.net/(\w+)/(.+)$', c[6] or '')
            if m:
                img = m.group(1) + ':' + m.group(2)
            elif c[3] in serie:
                img = 'en:' + serie[c[3]] + '/' + c[3] + '/' + c[0]
        e = r['idExpansion']
        sets.setdefault(e, {'n': f'Set {e}', 'id': '', 'c': None})
        th = r2(g.get('trend-holo'))
        out.append([pid, t[1][1] if t else '', clean_en(r['name']), e, t[1][0] if t else '', t[1][2] if t else '', cat,
                    0 if t else ver.get(pid, 0), trend, low, r2(g.get('avg')), r2(g.get('avg1')), r2(g.get('avg7')),
                    r2(g.get('avg30')), th if th else None, r2(g.get('avg30-holo')) or None, img, r2(g.get('low-holo')) or None])
    return {'f': ['id', 'de', 'en', 'exp', 'nr', 'rar', 'cat', 'ver', 'trend', 'low', 'avg', 'avg1', 'avg7', 'avg30',
                  'trendHolo', 'avg30Holo', 'img', 'lowHolo'],
            'sets': {str(k): v for k, v in sets.items()}, 'rows': out}


# ---------------------------------------------------------------- Verlauf
def update_history(state, catalog, date):
    """Verlauf je Produkt in Cent: v = Trend, l = Ab-Preis (günstigstes Angebot),
    m = Marktpreis (Ø 7 Tage Verkauf, ohne diesen Wert der Trend)."""
    hdir = os.path.join(state, 'history')
    shards = collections.defaultdict(lambda: {'v': {}, 'l': {}, 'm': {}})
    for r in catalog['rows']:
        sh = shards[r[0] % SHARDS]
        if r[8] is not None:
            sh['v'][str(r[0])] = int(round(r[8] * 100))
        if r[9] is not None:
            sh['l'][str(r[0])] = int(round(r[9] * 100))
        mk = r[12] if r[12] is not None else r[8]
        if mk is not None:
            sh['m'][str(r[0])] = int(round(mk * 100))
    d = dt.date.fromisoformat(date)
    for s in range(SHARDS):
        path = os.path.join(hdir, f'{s}.json')
        h = load(path, None) or {'d0': date, 'v': {}}
        h.setdefault('l', {})
        h.setdefault('m', {})
        d0 = dt.date.fromisoformat(h['d0'])
        idx = (d - d0).days
        if idx < 0:
            continue
        for key in ('v', 'l', 'm'):
            for pid, cents in shards[s][key].items():
                arr = h[key].setdefault(pid, [])
                if len(arr) < idx:
                    arr.extend([None] * (idx - len(arr)))
                if len(arr) == idx:
                    arr.append(cents)
                else:
                    arr[idx] = cents
        if idx + 1 > KEEP_DAYS:
            cut = idx + 1 - KEEP_DAYS
            h['d0'] = (d0 + dt.timedelta(days=cut)).isoformat()
            for key in ('v', 'l', 'm'):
                h[key] = {k: v[cut:] for k, v in h[key].items() if any(x is not None for x in v[cut:])}
        dump(path, h)


# ---------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', default='state')
    ap.add_argument('--site', default='site')
    ap.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()

    print('Lade Cardmarket …')
    pg = get_json(CM + 'priceGuide/price_guide_6.json')
    singles = get_json(CM + 'productList/products_singles_6.json')['products']
    nonsingles = get_json(CM + 'productList/products_nonsingles_6.json')['products']
    date = pg['createdAt'][:10]
    print('Price Guide', pg['createdAt'], len(pg['priceGuides']), 'Einträge')

    cards = refresh_tcgdex(a.state, os.path.join(a.root, 'data', 'tcgdex-de.json'), today)
    tsets = refresh_sets(a.state, today)
    cat = build_catalog(pg, singles, nonsingles, cards, tsets)
    cat['createdAt'] = pg['createdAt']
    cat['date'] = date
    print('Katalog:', len(cat['rows']), 'Produkte,', sum(1 for r in cat['rows'] if r[1]), 'mit deutschem Namen')

    update_history(a.state, cat, date)
    meta = load(os.path.join(a.state, 'meta.json'), {})
    days = sorted(set(meta.get('days', []) + [date]))[-KEEP_DAYS:]
    meta = {'date': date, 'createdAt': pg['createdAt'], 'built': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'products': len(cat['rows']), 'days': days, 'shards': SHARDS}
    dump(os.path.join(a.state, 'meta.json'), meta)

    # Website bauen
    if os.path.exists(a.site):
        shutil.rmtree(a.site)
    shutil.copytree(os.path.join(a.root, 'web'), a.site)
    dump(os.path.join(a.site, 'data', 'katalog.json'), cat)
    dump(os.path.join(a.site, 'data', 'meta.json'), meta)
    shutil.copytree(os.path.join(a.state, 'history'), os.path.join(a.site, 'data', 'h'))
    open(os.path.join(a.site, '.nojekyll'), 'w').close()
    print('Fertig:', meta)


if __name__ == '__main__':
    sys.exit(main())
