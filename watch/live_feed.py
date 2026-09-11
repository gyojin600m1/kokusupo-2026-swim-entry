#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SEIKO LiveResults（会場の電光掲示板と同じ内容）を8秒おきに読み、
泳ぎ終わった組のタイムを live_heats.json に貯める。update.py がこれを1分おきにアプリへ反映する。
launchd（KeepAlive）で回る。Claudeは使わない。"""
import json, os, re, subprocess, sys, time, urllib.request, unicodedata

HERE  = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, 'live_heats.json')
LOG   = os.path.join(HERE, 'live_log.txt')
URL   = 'http://swim2.seiko.co.jp/LiveResults/sw_result.xml'
LABEL = 'com.fukuda.swim.kokusupo2026-live'
STOP_AFTER = '2026-09-13 21:45'
WINDOW = {'2026-09-11': ('9:00', '19:30'), '2026-09-12': ('9:00', '19:30'), '2026-09-13': ('9:00', '17:30')}
EVERY = 8
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TIME = re.compile(r'^(?:\d+:)?\d{1,2}\.\d{2}$')


def log(m):
    with open(LOG, 'a') as f:
        f.write(time.strftime('%m/%d %H:%M:%S ') + m + '\n')


def in_window():
    today = time.strftime('%Y-%m-%d')
    win = WINDOW.get(today)
    if not win:
        return False
    mins = int(time.strftime('%H')) * 60 + int(time.strftime('%M'))
    h0, m0 = win[0].split(':'); h1, m1 = win[1].split(':')
    return int(h0) * 60 + int(m0) <= mins < int(h1) * 60 + int(m1)


def attrs(tag):
    return {k: unicodedata.normalize('NFKC', v) for k, v in re.findall(r'(\w+)="([^"]*)"', tag)}


def parse(xml):
    m = re.search(r'<evt\b[^>]*>', xml)
    if not m:
        return None
    ev = attrs(m.group(0))
    no = int(ev.get('no') or 0)
    hm = re.match(r'(\d+)', ev.get('hj', ''))
    if not no or not hm:
        return None
    lanes = {}
    for sw in re.findall(r'<swm\b[^>]*>', xml):
        a = attrs(sw)
        if not a.get('nj', '').strip():
            continue
        lanes[a['cno']] = {'name': a['nj'].strip(), 'team': a['tj'].replace(' ', '').strip(),
                           'tm': a.get('tm', '').strip(), 'r': a.get('r', '').strip(),
                           'rec': a.get('nrj', '').strip()}
    return {'no': no, 'heat': int(hm.group(1)), 'round': ev.get('rj', ''), 'gender': ev.get('gj', ''),
            'event': (ev.get('d', '') + ev.get('sj', '')).strip(), 'lanes': lanes}


def main():
    last_key, quiet = None, 0
    while True:
        now = time.strftime('%Y-%m-%d %H:%M')
        if now > STOP_AFTER:
            log('大会が終わったので止めた')
            subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], check=False)
            return
        if not in_window():
            time.sleep(60)
            continue
        try:
            req = urllib.request.Request(URL, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                xml = r.read().decode('shift_jis', 'replace')
            cur = parse(xml)
        except Exception as ex:
            quiet += 1
            if quiet in (1, 30):
                log('読めない: ' + repr(ex)[:80])
            time.sleep(EVERY)
            continue
        quiet = 0
        if cur:
            key = f"{cur['no']}|{cur['heat']}"
            if key != last_key:
                log(f"いま No.{cur['no']} {cur['gender']} {cur['event']} {cur['round']} 第{cur['heat']}組")
                last_key = key
            # 泳いでいる最中は tm にラップが入る。ゴールして順位(r)が付いたレーンだけを採る。
            timed = {l: v for l, v in cur['lanes'].items() if v['r']}
            if timed:
                try:
                    store = json.load(open(STORE, encoding='utf-8'))
                except Exception:
                    store = {}
                h = store.get(key) or {'no': cur['no'], 'heat': cur['heat'], 'round': cur['round'], 'lanes': {}}
                before = json.dumps(h['lanes'], sort_keys=True, ensure_ascii=False)
                h['lanes'].update(timed)
                h['t'] = int(time.time())
                if json.dumps(h['lanes'], sort_keys=True, ensure_ascii=False) != before:
                    store[key] = h
                    tmp = STORE + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(store, f, ensure_ascii=False)
                    os.replace(tmp, STORE)
                    log(f"  No.{cur['no']} 第{cur['heat']}組 {len(timed)}レーンのタイム")
        time.sleep(EVERY)


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        log('想定外のエラー: ' + repr(ex)[:200])
        raise
