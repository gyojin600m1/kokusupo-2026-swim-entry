#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第80回国民スポーツ大会 競泳（2026-09-11〜13 青森）速報の見張り。

  ・開催県の記録検索システム（kirokukensaku.net/5NS26/）から種目ごとの結果を取り、
    エントリー確認アプリに反映して公開する。
  ・予選は 組／コース で本人の行に付ける。決勝は本人の行を増やして付ける。
  ・検算に落ちたら公開しない。転載に関する掲示を見つけたら自動的に止まる。
  ・launchd から3分おきに動く（競技のある時間帯だけ）。
"""
import json, os, re, subprocess, sys, time, urllib.request, unicodedata

HERE   = os.path.dirname(os.path.abspath(__file__))
APP    = os.path.dirname(HERE)
SRC    = os.path.expanduser('~/swim-entry-app/samples/国スポ2026競泳.json')
BUILD  = os.path.expanduser('~/swim-entry-app/build.py')
OUT    = os.path.expanduser('~/swim-entry-app/out/国スポ2026競泳/index.html')
BACKUP = os.path.expanduser('~/swim-apps-backup/apps/kokusupo-2026-swim-entry/index.html')
LOG    = os.path.join(HERE, 'log.txt')
SEEN   = os.path.join(HERE, '.seen.json')
HALT   = os.path.join(HERE, '.halted')

LABEL  = 'com.fukuda.swim.kokusupo2026-results'
BASE   = 'https://kirokukensaku.net'
DAYS   = {'2026-09-11': '1日目', '2026-09-12': '2日目', '2026-09-13': '3日目'}
STOP_AFTER = '2026-09-13 21:45'
# 決勝終了 18:15 / 18:18 / 16:12。掲載は種目終了から1〜2時間遅れるので、その分だけ後ろまで。夜中は回さない。
WINDOW = {'2026-09-11': ('9:00', '21:00'),
          '2026-09-12': ('9:00', '21:00'),
          '2026-09-13': ('9:00', '19:30')}
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
NOTICE_WORDS = ['転載', '再配布', '再利用', '大会運営を目的']

# サイトの種別見出し → アプリの (distance の前置き, 性別)
CAT = {'成年男子': ('成年', '男子'), '成年女子': ('成年', '女子'),
       '少年男子Ａ': ('少年Ａ', '男子'), '少年女子Ａ': ('少年Ａ', '女子'),
       '少年男子Ｂ': ('少年Ｂ', '男子'), '少年女子Ｂ': ('少年Ｂ', '女子'),
       '少年女子': ('少年共通', '女子'), '少年男子': ('少年共通', '男子')}
IT = str.maketrans({'髙': '高', '﨑': '崎', '栁': '柳', '𠮷': '吉', '濵': '浜', '濱': '浜',
                    '邊': '辺', '邉': '辺', '齋': '斎', '齊': '斉', '國': '国', '槗': '橋',
                    '𣘺': '橋', '瀨': '瀬', '德': '徳', '黒': '黒', '眞': '真', '澤': '沢',
                    '廣': '広', '嶋': '島'})


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


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def halted():
    return os.path.exists(HALT)


def halt(reason):
    with open(HALT, 'w') as f:
        f.write(reason + '\n')
    log('停止: ' + reason)
    notify('国スポ競泳 見張り停止', reason)
    stop_myself()


# ---------- 文字の正規化 ----------
def nfkc(s):
    return unicodedata.normalize('NFKC', s or '')


def nname(s):
    # 姓名の間の空白と異体字を吸収して比べる
    return re.sub(r'[\s　]+', '', nfkc(s)).translate(IT)


def npref(s):
    # 「青森(青森県競技力向上対策本部)」→「青森」
    s = nfkc(s).strip()
    s = re.split(r'[（(]', s)[0].strip()
    return s


def nevent(s):
    # 「４×１００ｍフリーリレー」「400ｍ自由形」→ ('4×100m', 'フリーリレー') / ('400m','自由形')
    s = nfkc(s).replace(' ', '').replace('x', '×').replace('X', '×').replace('Ｘ', '×')
    m = re.match(r'^(\d+×\d+m|\d+m)(.+)$', s)
    return (m.group(1), m.group(2)) if m else (None, s)


def ntime(s):
    # 「4分16秒08」→「4:16.08」、「58秒12」→「58.12」
    s = nfkc(s).strip()
    m = re.match(r'^(?:(\d+)分)?(\d+)秒(\d+)$', s)
    if not m:
        return None
    mm, ss, cc = m.groups()
    return (f'{int(mm)}:{int(ss):02d}.{cc}' if mm else f'{int(ss)}.{cc}')


def strip(x):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', x)).strip()


# ---------- サイトの読み取り ----------
def parse_discipline(html):
    """種別ごとの表 → [(種別, 開始, 種目, 状況, href)]"""
    out = []
    parts = re.split(r'<h3 class="result_border_title">', html)
    for part in parts[1:]:
        cat = strip(part.split('</h3>', 1)[0])
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', part, re.S):
            cells = [strip(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)]
            href = re.findall(r'href="(/5NS26/detail_[^"]+)"', tr)
            if len(cells) >= 4 and href:
                out.append((cat, cells[0], cells[1], cells[3], href[0]))
    return out


def parse_detail(html):
    """種目ページ → (見出し, [(順位, 組, コース, 名前, 所属, 記録, 備考)])"""
    title = re.findall(r'<h3[^>]*>([^<]*?(?:予選|決勝)[^<]*)</h3>', html)
    title = strip(title[0]) if title else ''
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
        rank = int(rank) if re.match(r'^\d+$', nfkc(rank)) else None
        rows.append((rank, heat, lane, name, team, rec, note))
    return title, rows


def make_result(rank, rec, note):
    r = {}
    t = ntime(rec)
    if t:
        r['time'] = t
    if rank:
        r['rank'] = rank
    n = nfkc(note).strip()
    if re.search(r'棄権|失格|途中|DNS|DSQ|DNF|DQ', n, re.I):
        r['note'] = n
        r.pop('rank', None)
    elif re.search(r'新', n):
        r['rec'] = n
    if '決勝へ' in n or n in ('Q', 'q'):
        r['adv'] = True
    if not t and not r.get('note') and rec.strip() and not re.match(r'^[-－―—]*$', rec.strip()):
        r['note'] = nfkc(rec).strip()      # 記録欄に「失格」等が入る形
    return r


# ---------- 本体 ----------
def main():
    now = time.strftime('%Y-%m-%d %H:%M')
    if now > STOP_AFTER:                       # 停止判定は時間帯ガードより先に置く
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
    # (前置き, 性別, 距離, 泳法, ラウンド) → No.
    pkey = {}
    for p in prog:
        pre, dist = p['distance'].split(' ', 1)
        pkey[(pre, p['gender'], dist.replace('ｍ', 'm'), p['stroke'], p['round'])] = p['no']
    is_relay_no = {p['no'] for p in prog if 'リレー' in p['stroke']}
    pinfo = {p['no']: p for p in prog}

    seen = load(SEEN, {})
    changed = 0
    unmatched = []

    for day, dayname in DAYS.items():
        if day > today:
            continue
        url = f'{BASE}/5NS26/discipline_020_{day.replace("-", "")}.html'
        try:
            html = get(url)
        except Exception as ex:
            log(f'取得失敗 {url}: {repr(ex)[:80]}')
            continue
        if any(w in html for w in NOTICE_WORDS):
            halt('記録検索システムに転載に関する掲示が出た')
            return
        for cat, t0, ev, status, href in parse_discipline(html):
            if '終了' not in status:
                continue
            if cat not in CAT:
                unmatched.append(f'種別不明 {cat} {ev}')
                continue
            pre, gen = CAT[cat]
            dist, stroke = nevent(re.sub(r'\s*(予選|決勝)\s*$', '', ev))
            rnd = '決勝' if '決勝' in ev else '予選'
            no = pkey.get((pre, gen, dist, stroke, rnd))
            if no is None:
                unmatched.append(f'種目不明 {cat} {ev} → {(pre, gen, dist, stroke, rnd)}')
                continue
            try:
                dhtml = get(BASE + href)
            except Exception as ex:
                log(f'取得失敗 {href}: {repr(ex)[:80]}')
                continue
            sig = str(len(dhtml)) + '|' + str(dhtml.count('rl_rank'))
            if seen.get(href) == sig and not byhand:
                continue                              # 前回と同じ内容
            title, rows = parse_detail(dhtml)
            if not rows:
                continue
            relay = no in is_relay_no
            pool = d['relays'] if relay else d['entries']
            n_ok = 0
            for rank, heat, lane, name, team, rec, note in rows:
                pref = npref(team if relay else team)
                res = make_result(rank, rec, note)
                if rnd == '予選':
                    cand = [e for e in pool if no in (e.get('programNos') or [])
                            and e.get('heat') == heat and e.get('lane') == lane]
                    ok = [e for e in cand if (relay and npref(e.get('team')) == pref)
                          or (not relay and nname(e.get('name')) == nname(name))]
                    if not ok:                        # 組・コースで違えば名前（都道府県）で探す
                        ok = [e for e in pool if no in (e.get('programNos') or [])
                              and ((relay and npref(e.get('team')) == pref)
                                   or (not relay and nname(e.get('name')) == nname(name)
                                       and npref(e.get('team')) == pref))]
                        if ok and heat and lane:
                            ok[0]['heat'], ok[0]['lane'] = heat, lane
                            ok[0]['note'] = f'第{heat}組 {lane}レーン'
                    if not ok:
                        unmatched.append(f'No.{no} {heat}/{lane} {name} {team}')
                        continue
                    e = ok[0]
                else:  # 決勝: 予選の行を型にして決勝の行を作る（既にあれば更新）
                    pre_no = pkey.get((pre, gen, dist, stroke, '予選'))
                    base = [e for e in pool if pre_no in (e.get('programNos') or [])
                            and ((relay and npref(e.get('team')) == pref)
                                 or (not relay and nname(e.get('name')) == nname(name)
                                     and npref(e.get('team')) == pref))]
                    if not base:
                        unmatched.append(f'No.{no}(決勝) {name} {team}')
                        continue
                    have = [e for e in pool if no in (e.get('programNos') or [])
                            and ((relay and npref(e.get('team')) == pref)
                                 or (not relay and nname(e.get('name')) == nname(name)))]
                    if have:
                        e = have[0]
                    else:
                        e = {k: v for k, v in base[0].items() if k not in ('result', 'heat', 'lane', 'note')}
                        e['programNos'] = [no]
                        pool.append(e)
                        changed += 1
                    if heat and lane:
                        e['heat'], e['lane'] = heat, lane
                        e['note'] = f'決勝 {lane}レーン'
                if e.get('result') != res:
                    e['result'] = res
                    changed += 1
                n_ok += 1
            seen[href] = sig
            log(f'No.{no} {cat} {ev}: {n_ok}/{len(rows)}件')

    if unmatched:
        log('  未突合: ' + ' / '.join(unmatched[:8]) + (' …' if len(unmatched) > 8 else ''))

    # --- 速報のまとめを選手タブの先頭に出す（時計は入れない） ---
    fin = sorted({n for e in d['entries'] + d['relays'] if e.get('result') for n in (e.get('programNos') or [])})
    if fin:
        last = fin[-1]
        p = pinfo[last]
        pool = d['relays'] if last in is_relay_no else d['entries']
        top = sorted([e for e in pool if last in (e.get('programNos') or []) and (e.get('result') or {}).get('rank')],
                     key=lambda e: e['result']['rank'])[:3]
        # メダルは決勝だけ。予選は順位の数字で出す。
        medal = {1: '🥇', 2: '🥈', 3: '🥉'} if p['round'] == '決勝' else {1: '1位 ', 2: '2位 ', 3: '3位 '}
        line = '　'.join(f"{medal.get(e['result']['rank'], '')}{e.get('name') or e.get('team')} {e['result'].get('time', '')}"
                         for e in top)
        note = (f"🔴 **速報中**　終了 {len(fin)}/106種目\n"
                f"**直近 No.{last} {p['gender']} {p['distance']} {p['stroke']} {p['round']}**"
                + (("\n" + line) if line else ""))
        if len(fin) >= 106:
            note = f"🏁 **全106種目 終了**\n記録は速報値です。正式な結果は公式の発表でご確認ください。"
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
