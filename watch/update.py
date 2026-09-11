#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第80回国民スポーツ大会 競泳（2026-09-11〜13 青森）速報の見張い。

  ・SEIKO 競技結果速報 ranking/{日}R{No}.pdf（種目終了の数分後に出る）を本線にする。
  ・開催県の記録検索システム（kirokukensaku.net/5NS26/・1〜2時間遅れ）は
    「決勝へ」「新記録」の確認だけに使う（SEIKOの記録・順位は上書きしない）。
  ・予選は 組／水路 で本人の行に付ける。決勝は本人の行を増やして付ける。リレーは泳者も入れる。
  ・検算に落ちたら公開しない。転載に関する掲示を見つけたら自動的に止まる。
  ・launchd から1分おきに動く（競技のある時間帯だけ）。Claudeは使わない。
"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, unicodedata, collections, io

HERE   = os.path.dirname(os.path.abspath(__file__))
APP    = os.path.dirname(HERE)
SRC    = os.path.expanduser('~/swim-entry-app/samples/国スポ2026競泳.json')
BUILD  = os.path.expanduser('~/swim-entry-app/build.py')
OUT    = os.path.expanduser('~/swim-entry-app/out/国スポ2026競泳/index.html')
BACKUP = os.path.expanduser('~/swim-apps-backup/apps/kokusupo-2026-swim-entry/index.html')
LOG    = os.path.join(HERE, 'log.txt')
SEEN   = os.path.join(HERE, '.seen.json')
HALT   = os.path.join(HERE, '.halted')
PDFDIR = os.path.join(HERE, '.pdf')

LABEL  = 'com.fukuda.swim.kokusupo2026-results'
SEIKO  = 'https://swim.seiko.co.jp/2026/S70703'
KIROKU = 'https://kirokukensaku.net'
DAYS   = {'2026-09-11': 1, '2026-09-12': 2, '2026-09-13': 3}
FINAL_N = 8                                    # 予選→決勝は8名（記録検索の「決勝へ」が8つ）
STOP_AFTER = '2026-09-13 21:45'
WINDOW = {'2026-09-11': ('9:00', '21:00'),
          '2026-09-12': ('9:00', '21:00'),
          '2026-09-13': ('9:00', '19:30')}
RECHECK = 15 * 60                              # 取り込み済みの種目は15分に1回だけ見直す（訂正対応）
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
NOTICE_WORDS = ['転載', '再配布', '再利用', '大会運営を目的']
TIME = re.compile(r'^(?:\d+:)?\d{1,2}\.\d{2}$')
NG = ('棄権', '失格', '途中棄権', '不出場')

CAT = {'成年男子': ('成年', '男子'), '成年女子': ('成年', '女子'),
       '少年男子A': ('少年Ａ', '男子'), '少年女子A': ('少年Ａ', '女子'),
       '少年男子B': ('少年Ｂ', '男子'), '少年女子B': ('少年Ｂ', '女子'),
       '少年女子': ('少年共通', '女子'), '少年男子': ('少年共通', '男子')}   # 見出しはNFKCしてから引く
IT = str.maketrans({'髙': '高', '﨑': '崎', '栁': '柳', '𠮷': '吉', '濵': '浜', '濱': '浜',
                    '邊': '辺', '邉': '辺', '齋': '斎', '齊': '斉', '國': '国', '槗': '橋',
                    '𣘺': '橋', '瀨': '瀬', '德': '徳', '眞': '真', '澤': '沢', '廣': '広', '嶋': '島',
                    '桒': '桑', '靑': '青', '曾': '曽', '寳': '宝', '壽': '寿', '惠': '恵', '榮': '栄',
                    '龍': '竜', '澁': '渋', '舘': '館', '冨': '富', '峯': '峰', '條': '条', '萬': '万',
                    '內': '内', '淸': '清', '祐': '祐', '﨑': '崎', '嵜': '崎', '瀧': '滝', '櫻': '桜',
                    '眞': '真', '晄': '晃', '皓': '皓', '琉': '琉', '愼': '慎', '禮': '礼', '龝': '秋'})


def log(m):
    with open(LOG, 'a') as f:
        f.write(time.strftime('%m/%d %H:%M ') + m + '\n')


def notify(title, msg):
    subprocess.run(['osascript', '-e',
        f'display notification "{msg[:200].replace(chr(34), chr(39)).replace(chr(10), " ")}" '
        f'with title "{title.replace(chr(34), chr(39))}" sound name "Glass"'], check=False)


def stop_myself():
    subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], check=False)


def load(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def halted():
    return os.path.exists(HALT)


def halt(reason):
    with open(HALT, 'w') as f:
        f.write(reason + '\n')
    log('停止: ' + reason)
    notify('国スポ競泳 見張り停止', reason)
    stop_myself()


def get(url, since=None, timeout=30):
    """(status, body, last-modified)。変更が無ければ (304, None, since)。"""
    h = {'User-Agent': UA}
    if since:
        h['If-Modified-Since'] = since
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get('Last-Modified')
    except urllib.error.HTTPError as ex:
        if ex.code == 304:
            return 304, None, since
        return ex.code, None, None


# ---------- 文字の正規化 ----------
def nfkc(s):
    return unicodedata.normalize('NFKC', s or '')


def nname(s):
    return re.sub(r'[\s　]+', '', nfkc(s)).translate(IT)


def npref(s):
    return re.split(r'[（(]', nfkc(s).strip())[0].replace(' ', '').strip()


def nevent(s):
    s = nfkc(s).replace(' ', '').replace('x', '×').replace('X', '×')
    m = re.match(r'^(\d+×\d+m|\d+m)(.+)$', s)
    return (m.group(1), m.group(2)) if m else (None, s)


def ntime(s):
    s = nfkc(s).strip()
    m = re.match(r'^(?:(\d+)分)?(\d+)秒(\d+)$', s)
    if not m:
        return None
    mm, ss, cc = m.groups()
    return (f'{int(mm)}:{int(ss):02d}.{cc}' if mm else f'{int(ss)}.{cc}')


def strip(x):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', x)).strip()


# ---------- SEIKO 種目別競技結果 PDF ----------
def parse_seiko(data, relay):
    """PDF → (見出し, [row])。row = {rank, heat, lane, name/team, pref, time, note, rec, members}"""
    import pdfplumber
    head, rows, cur = '', [], None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first = pdf.pages[0].extract_text() or ''
        if 'まだ作成されていません' in first or '実施されません' in first:
            return None, []
        for page in pdf.pages:
            line = collections.defaultdict(list)
            for w in page.extract_words():
                line[round(w['top'] / 2)].append(w)
            for k in sorted(line):
                ws = sorted(line[k], key=lambda w: w['x0'])
                txt = ' '.join(w['text'] for w in ws)
                if txt.startswith('競技No.'):
                    head = txt
                    continue
                hl = [w['text'] for w in ws if 55 <= w['x0'] < 106 and re.fullmatch(r'\d+/\d+', w['text'])]
                if hl:                                          # 出場者の1行目
                    rank = [w['text'] for w in ws if w['x0'] < 55 and re.fullmatch(r'\d+', w['text'])]
                    h, l = hl[0].split('/')
                    cur = {'heat': int(h), 'lane': int(l), 'rank': int(rank[0]) if rank else None,
                           'time': None, 'note': None, 'rec': None, 'members': []}
                    if relay:
                        cur['pref'] = ''.join(w['text'] for w in ws if 85 <= w['x0'] < 148)
                        cur['name'] = cur['pref']
                    else:
                        cur['pref'] = ''.join(w['text'] for w in ws if 106 <= w['x0'] < 146)
                        cur['name'] = ''.join(w['text'] for w in ws if 146 <= w['x0'] < 214)
                    rows.append(cur)
                if cur is None:
                    continue
                if relay and not hl:                             # 泳者の行（泳順 1〜4 が x≈89）
                    od = [w for w in ws if 80 <= w['x0'] < 100 and re.fullmatch(r'[1-4]', w['text'])]
                    if od and len(cur['members']) < 4:
                        nm = ' '.join(w['text'] for w in ws if 100 <= w['x0'] < 186)
                        if nm:
                            cur['members'].append(nfkc(nm))
                for w in ws:
                    if 430 <= w['x0'] < 527:
                        if TIME.fullmatch(w['text']) and cur['time'] is None:
                            cur['time'] = w['text']
                        elif w['text'] in NG:
                            cur['note'] = w['text']
                    if w['x0'] < 60 and w['text'] in NG:
                        cur['note'] = w['text']
                    if w['x0'] >= 430 and re.fullmatch(r'\S*新', w['text']):
                        cur['rec'] = w['text']
    return head, rows


def head_ok(head, p):
    """「競技No. : 15 女子 4x100m フリーリレー 予選」がアプリの種目と合っているか"""
    h = nfkc(head).replace(' ', '')
    m = re.search(r'競技No\.:(\d+)', h)
    return bool(m) and int(m.group(1)) == p['no'] and p['gender'] in h \
        and p['stroke'] in h and p['round'] in h


# ---------- 記録検索システム（決勝へ・新記録の確認だけ） ----------
def parse_discipline(html):
    out = []
    for part in re.split(r'<h3 class="result_border_title">', html)[1:]:
        cat = strip(part.split('</h3>', 1)[0])
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', part, re.S):
            cells = [strip(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
            href = re.findall(r'href="(/5NS26/detail_[^"]+)"', tr)
            if len(cells) >= 4 and href:
                out.append((cat, cells[1], cells[3], href[0]))
    return out


def parse_detail(html):
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        if 'rl_rank' not in tr or '<th' in tr:
            continue
        cells = [strip(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
        if len(cells) < 6:
            continue
        rank, hl, name, team, rec, note = cells[:6]
        m = re.match(r'^(\d+)\s*/\s*(\d+)$', nfkc(hl))
        heat, lane = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        rows.append((heat, lane, name, team, rec, nfkc(note).strip()))
    return rows


# ---------- 本人の行に付ける ----------
def find_row(pool, no, heat, lane, name, pref, relay):
    ok = [e for e in pool if no in (e.get('programNos') or []) and e.get('heat') == heat and e.get('lane') == lane
          and ((relay and npref(e.get('team')) == npref(pref))
               or (not relay and nname(e.get('name')) == nname(name)))]
    if ok:
        return ok[0], False
    ok = [e for e in pool if no in (e.get('programNos') or [])
          and ((relay and npref(e.get('team')) == npref(pref))
               or (not relay and nname(e.get('name')) == nname(name) and npref(e.get('team')) == npref(pref)))]
    if ok:
        return ok[0], True
    ok = [e for e in pool if no in (e.get('programNos') or []) and e.get('heat') == heat and e.get('lane') == lane
          and npref(e.get('team')) == npref(pref)]
    if ok:
        log(f'  名前違い（組・水路と県で確定）No.{no} {heat}/{lane} PDF「{name}」/ 名簿「{ok[0].get("name")}」')
        return ok[0], False
    return None, False


def final_row(pool, pkey, no, key, name, pref, relay, heat, lane):
    """決勝の行。予選の行を型にして作る（既にあれば返す）"""
    pre_no = pkey.get(key[:4] + ('予選',))
    base = [e for e in pool if pre_no in (e.get('programNos') or [])
            and ((relay and npref(e.get('team')) == npref(pref))
                 or (not relay and nname(e.get('name')) == nname(name) and npref(e.get('team')) == npref(pref)))]
    if not base:
        return None, False
    have = [e for e in pool if no in (e.get('programNos') or [])
            and ((relay and npref(e.get('team')) == npref(pref)) or (not relay and nname(e.get('name')) == nname(name)))]
    if have:
        return have[0], False
    e = {k: v for k, v in base[0].items() if k not in ('result', 'heat', 'lane', 'note', 'members')}
    e['programNos'] = [no]
    pool.append(e)
    return e, True


def main():
    now = time.strftime('%Y-%m-%d %H:%M')
    if now > STOP_AFTER:
        log('大会が終わったので見張りを止めた')
        notify('国スポ競泳', '見張りを終了しました')
        stop_myself()
        return
    if halted():
        return
    byhand = '--now' in sys.argv
    today = time.strftime('%Y-%m-%d')
    mins = int(time.strftime('%H')) * 60 + int(time.strftime('%M'))
    win = WINDOW.get(today)
    if not byhand:
        if not win:
            return
        h0, m0 = win[0].split(':'); h1, m1 = win[1].split(':')
        if not (int(h0) * 60 + int(m0) <= mins < int(h1) * 60 + int(m1)):
            return

    d = json.load(open(SRC, encoding='utf-8'))
    prog = d['program']
    pinfo = {p['no']: p for p in prog}
    pkey, keyof = {}, {}
    for p in prog:
        pre, dist = p['distance'].split(' ', 1)
        k = (pre, p['gender'], dist.replace('ｍ', 'm'), p['stroke'], p['round'])
        pkey[k] = p['no']; keyof[p['no']] = k
    is_relay = {p['no'] for p in prog if 'リレー' in p['stroke']}
    dayno = {'1日目': 1, '2日目': 2, '3日目': 3}

    seen = load(SEEN, {})
    st = seen.setdefault('seiko', {})
    changed, unmatched, imported = 0, [], []
    os.makedirs(PDFDIR, exist_ok=True)
    tnow = time.time()

    # ===== 1) SEIKO 本線 =====
    for p in prog:
        no, dn = p['no'], dayno.get(p['day'], 1)
        if dn > DAYS.get(today, 9):
            continue                                     # まだ来ていない日
        s = st.setdefault(str(no), {})
        if dn == DAYS.get(today) and p.get('startTime'):
            h, m = p['startTime'].split(':')
            if mins < int(h) * 60 + int(m) and not byhand:
                continue                                 # まだ泳いでいない
        if s.get('done') and tnow - s.get('t', 0) < RECHECK and not byhand:
            continue
        url = f'{SEIKO}/ranking/{dn:02d}R{no:03d}.pdf'
        code, data, lm = get(url, None if byhand else s.get('lm'))   # 手動のときは必ず取り直す
        s['t'] = tnow
        if code == 304:
            continue
        if code != 200 or not data:
            if code not in (404,):
                log(f'取得失敗 {url}: HTTP {code}')
            continue
        if len(data) < 12000:                             # 「まだ作成されていません」
            s['lm'] = lm
            continue
        relay = no in is_relay
        try:
            head, rows = parse_seiko(data, relay)
        except Exception as ex:
            log(f'No.{no} PDFを読めない: {repr(ex)[:80]}')
            continue
        if head is None:
            s['lm'] = lm
            continue
        if not head_ok(head, p):
            log(f'No.{no} 見出しが合わない: {head[:60]}')
            continue
        with open(os.path.join(PDFDIR, f'{dn:02d}R{no:03d}.pdf'), 'wb') as f:
            f.write(data)
        pool = d['relays'] if relay else d['entries']
        n_ok = 0
        for r in rows:
            res = {}
            if r['time']:
                res['time'] = r['time']
            if r['note']:
                res['note'] = r['note']
            elif r['rank']:
                res['rank'] = r['rank']
                if p['round'] == '予選' and r['rank'] <= FINAL_N:
                    res['adv'] = True
            if r['rec']:
                res['rec'] = r['rec']
            if p['round'] == '予選':
                e, moved = find_row(pool, no, r['heat'], r['lane'], r['name'], r['pref'], relay)
                if e is None:
                    unmatched.append(f'No.{no} {r["heat"]}/{r["lane"]} {r["name"]} {r["pref"]}')
                    continue
                if moved:
                    e['heat'], e['lane'] = r['heat'], r['lane']
                    e['note'] = f'第{r["heat"]}組 {r["lane"]}レーン'
                    changed += 1
            else:
                e, made = final_row(pool, pkey, no, keyof[no], r['name'], r['pref'], relay, r['heat'], r['lane'])
                if e is None:
                    unmatched.append(f'No.{no}(決勝) {r["name"]} {r["pref"]}')
                    continue
                if made:
                    changed += 1
                if e.get('lane') != r['lane']:
                    e['heat'], e['lane'] = r['heat'], r['lane']
                    e['note'] = f'決勝 {r["lane"]}レーン'
                    changed += 1
            old = e.get('result') or {}
            new = dict(old)
            new.update(res)
            for k in ('rank', 'adv'):
                if k in old and k not in res:
                    new.pop(k, None)                      # 失格に変わった等
            if new != old:
                e['result'] = new
                changed += 1
            if relay and r['members'] and e.get('members') != r['members']:
                e['members'] = r['members']
                changed += 1
            n_ok += 1
        s['lm'] = lm
        s['done'] = n_ok > 0
        imported.append(no)
        log(f'No.{no} {p["gender"]} {p["distance"]} {p["stroke"]} {p["round"]}: {n_ok}/{len(rows)}件（SEIKO）')

    # ===== 2) 記録検索システム：決勝へ・新記録・棄権の確認（記録と順位は上書きしない）=====
    kr = seen.setdefault('kiroku', {})
    for day, dn in DAYS.items():
        if day > today:
            continue
        code, body, _ = get(f'{KIROKU}/5NS26/discipline_020_{day.replace("-", "")}.html')
        if code != 200 or not body:
            continue
        html = body.decode('utf-8', 'replace')
        if any(w in html for w in NOTICE_WORDS):
            halt('記録検索システムに転載に関する掲示が出た')
            return
        for cat, ev, status, href in parse_discipline(html):
            cat = nfkc(cat).replace(' ', '')
            if '終了' not in status or cat not in CAT:
                continue
            pre, gen = CAT[cat]
            dist, stroke = nevent(re.sub(r'\s*(予選|決勝)\s*$', '', ev))
            rnd = '決勝' if '決勝' in ev else '予選'
            no = pkey.get((pre, gen, dist, stroke, rnd))
            if no is None:
                continue
            code, body, _ = get(KIROKU + href)
            if code != 200 or not body:
                continue
            dhtml = body.decode('utf-8', 'replace')
            sig = f'{len(dhtml)}|{dhtml.count("rl_rank")}'
            if kr.get(href) == sig and not byhand:
                continue
            relay = no in is_relay
            pool = d['relays'] if relay else d['entries']
            for heat, lane, name, team, rec, note in parse_detail(dhtml):
                cand = [e for e in pool if no in (e.get('programNos') or [])
                        and ((relay and npref(e.get('team')) == npref(team))
                             or (not relay and nname(e.get('name')) == nname(name)))]
                if not cand:
                    continue
                e = cand[0]
                res = dict(e.get('result') or {})
                if e.get('result') is None and ntime(rec):        # SEIKOがまだなら記録も入れる
                    res['time'] = ntime(rec)
                adv = ('決勝へ' in note)
                if rnd == '予選' and res.get('adv', False) != adv and 'rank' in res:
                    res['adv'] = adv
                if re.search(r'新', note) and not res.get('rec'):
                    res['rec'] = note
                if re.search(r'棄権|失格|途中', note) and not res.get('note'):
                    res['note'] = note
                    res.pop('rank', None); res.pop('adv', None)
                if res and res != (e.get('result') or {}):
                    e['result'] = res
                    changed += 1
            kr[href] = sig

    if unmatched:
        log('  未突合: ' + ' / '.join(unmatched[:8]) + (' …' if len(unmatched) > 8 else ''))

    # ===== 3) 速報のまとめ（時計は入れない）=====
    fin = sorted({n for e in d['entries'] + d['relays'] if e.get('result') for n in (e.get('programNos') or [])})
    if fin:
        last = fin[-1]
        p = pinfo[last]
        pool = d['relays'] if last in is_relay else d['entries']
        top = sorted([e for e in pool if last in (e.get('programNos') or []) and (e.get('result') or {}).get('rank')],
                     key=lambda e: e['result']['rank'])[:3]
        medal = {1: '🥇', 2: '🥈', 3: '🥉'} if p['round'] == '決勝' else {1: '1位 ', 2: '2位 ', 3: '3位 '}
        line = '　'.join(f"{medal.get(e['result']['rank'], '')}{e.get('name') or e.get('team')} {e['result'].get('time', '')}"
                         for e in top)
        note = (f"🔴 **速報中**　終了 {len(fin)}/106種目\n"
                f"**直近 No.{last} {p['gender']} {p['distance']} {p['stroke']} {p['round']}**"
                + (("\n" + line) if line else ""))
        if len(fin) >= 106:
            note = "🏁 **全106種目 終了**\n記録は速報値です。正式な結果は公式の発表でご確認ください。"
        if d['meta'].get('notice') != note:
            d['meta']['notice'] = note
            changed += 1

    if not changed:
        json.dump(seen, open(SEEN, 'w'))
        return

    json.dump(d, open(SRC, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    b = subprocess.run([sys.executable, BUILD, SRC], capture_output=True, text=True)
    if b.returncode != 0 or '✗' in b.stdout:
        log('  ⚠ ビルドの検算に落ちた: ' + (b.stdout or b.stderr)[-200:])
        notify('国スポ競泳 要確認', 'ビルドの検算に落ちたので公開していません')
        return
    subprocess.run(['cp', OUT, os.path.join(APP, 'index.html')], check=False)
    subprocess.run(['cp', OUT, BACKUP], check=False)
    n_res = sum(1 for e in d['entries'] if e.get('result')) + sum(1 for r in d['relays'] if r.get('result'))
    with open(os.path.join(APP, 'live.json'), 'w') as f:
        json.dump({'n': n_res, 't': time.strftime('%H:%M')}, f)
    subprocess.run(['git', 'add', '-A'], cwd=APP, capture_output=True)
    msg = f'速報を反映（{len(fin)}/106種目・結果{n_res}件） {time.strftime("%H:%M")}'
    cm = subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=APP, capture_output=True, text=True)
    if cm.returncode == 0:
        ps = subprocess.run(['git', 'push', '-q'], cwd=APP, capture_output=True, text=True)
        log(f'  {msg} → ' + ('公開した' if ps.returncode == 0 else 'pushに失敗（次回やり直す）'))
    json.dump(seen, open(SEEN, 'w'))


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        log('想定外のエラー: ' + repr(ex)[:200])
        raise
