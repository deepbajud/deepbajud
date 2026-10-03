#!/usr/bin/env python3
"""Refresh repository-hosted profile graphics with GitHub data (stdlib only)."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'assets'
BG = '#0b111b'
TEXT = '#edf6ff'
MUTED = '#8ba2ba'
MINT = '#65f0be'
BLUE = '#70bbff'


def request(url, token='', payload=None):
    headers = {'User-Agent': 'deepbajud-profile', 'Accept': 'application/vnd.github+json'}
    if token and url.startswith('https://api.github.com/'):
        headers['Authorization'] = f'Bearer {token}'
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        return response.read().decode()


class CalendarParser(HTMLParser):
    """Fallback for GitHub's public contribution calendar; reject missing data."""
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tips = {}
        self.tip_id = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'td' and attrs.get('data-date'):
            self.cells[attrs['id']] = {'date': attrs['data-date'],
                                      'level': int(attrs.get('data-level', 0))}
        if tag == 'tool-tip' and attrs.get('for'):
            self.tip_id = attrs['for']
            self.tips[self.tip_id] = ''

    def handle_data(self, data):
        if self.tip_id:
            self.tips[self.tip_id] += data

    def handle_endtag(self, tag):
        if tag == 'tool-tip':
            self.tip_id = None

    def days(self):
        result = []
        for key, value in self.cells.items():
            tip = self.tips.get(key, '').strip()
            if tip.startswith('No contributions'):
                count = 0
            else:
                match = re.match(r'([\d,]+) contributions?\b', tip)
                if not match:
                    raise ValueError('GitHub calendar markup changed; preserving previous graphics.')
                count = int(match.group(1).replace(',', ''))
            result.append({**value, 'count': count})
        if len(result) < 350:
            raise ValueError('Incomplete GitHub contribution calendar; preserving previous graphics.')
        return sorted(result, key=lambda item: item['date'])


def collect(username, cachedir=None):
    token = os.environ.get('GITHUB_TOKEN', '')
    if cachedir:
        cachedir = Path(cachedir)
        user = json.loads((cachedir / 'user').read_text())
        repos = json.loads((cachedir / 'repos').read_text())
        parser = CalendarParser()
        parser.feed((cachedir / 'contributions').read_text())
        days = parser.days()
    else:
        user = json.loads(request(f'https://api.github.com/users/{username}', token))
        repos = []
        page = 1
        while True:
            batch = json.loads(request(f'https://api.github.com/users/{username}/repos?per_page=100&type=owner&page={page}', token))
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        days = None
        if token:
            query = '''query($login: String!) {
              user(login: $login) {
                contributionsCollection {
                  contributionCalendar {
                    weeks { contributionDays { date contributionCount contributionLevel } }
                  }
                }
              }
            }'''
            try:
                result = json.loads(request('https://api.github.com/graphql', token,
                                            {'query': query, 'variables': {'login': username}}))
                if result.get('errors'):
                    raise ValueError('GraphQL unavailable')
                calendar = result['data']['user']['contributionsCollection']['contributionCalendar']
                levels = {'NONE': 0, 'FIRST_QUARTILE': 1, 'SECOND_QUARTILE': 2,
                          'THIRD_QUARTILE': 3, 'FOURTH_QUARTILE': 4}
                days = [{'date': day['date'], 'count': day['contributionCount'],
                         'level': levels[day['contributionLevel']]}
                        for week in calendar['weeks'] for day in week['contributionDays']]
                if len(days) < 350:
                    raise ValueError('Incomplete calendar')
            except (urllib.error.URLError, ValueError, KeyError, TypeError):
                print('GraphQL unavailable; using the public GitHub calendar.', file=sys.stderr)
        if days is None:
            parser = CalendarParser()
            parser.feed(request(f'https://github.com/users/{username}/contributions'))
            days = parser.days()
    today = datetime.now(timezone.utc).date().isoformat()
    days = sorted((day for day in days if day['date'] <= today), key=lambda item: item['date'])
    if not days or len({d['date'] for d in days}) != len(days):
        raise ValueError('Invalid calendar dates')
    dates = [date.fromisoformat(day['date']) for day in days]
    if any((later-earlier).days != 1 for earlier,later in zip(dates,dates[1:])):
        raise ValueError('Missing calendar dates; preserving previous graphics.')
    if any(not isinstance(day['count'],int) or day['count'] < 0 or day['level'] not in range(5) for day in days):
        raise ValueError('Invalid contribution counts; preserving previous graphics.')
    return {'username': username, 'updated': today, 'user': user, 'repos': repos,
            'days': days, 'source': 'GitHub public profile and API'}


def text(x, y, value, size=16, fill=TEXT, weight=400, extra=''):
    return f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" {extra}>{escape(str(value))}</text>'


def document(width, height, title, body, styles=''):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">Real GitHub public data. The last successful snapshot remains visible if an update fails.</desc>
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#10202d"/><stop offset="1" stop-color="#090f18"/></linearGradient></defs>
<style>text{{font-family:Arial,Helvetica,sans-serif}} .mono{{font-family:Consolas,monospace}} {styles}
@media(prefers-reduced-motion:reduce){{.motion{{animation:none!important}}}}</style>
<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="22" fill="url(#bg)" stroke="#253647"/>
{body}</svg>'''


def stats(data):
    repos = [repo for repo in data['repos'] if not repo['fork']]
    days = data['days']
    metrics = [(data['user']['public_repos'], 'PUBLIC REPOSITORIES'),
               (sum(repo['stargazers_count'] for repo in repos), 'STARS · OWN REPOSITORIES'),
               (data['user']['followers'], 'FOLLOWERS'),
               (sum(day['count'] for day in days), 'CONTRIBUTIONS · PAST YEAR')]
    items = [text(40, 40, 'GITHUB / AT A GLANCE', 16, MUTED, 600, 'letter-spacing="2"'),
             text(1240, 40, f'UPDATED {data["updated"]} UTC', 15, MUTED, 400, 'text-anchor="end"')]
    for i, (value, label) in enumerate(metrics):
        x = 40 + i * 312
        if i:
            items.append(f'<path d="M{x-22} 65v94" stroke="#293949"/>')
        items += [text(x, 116, f'{value:,}', 51, MINT if i == 3 else TEXT, 700),
                  text(x, 149, label, 14, MUTED, 600, 'letter-spacing=".6"')]
    return document(1280, 184, 'Deep Golakiya — GitHub statistics', ''.join(items))


def stats_mobile(data):
    owned = [repo for repo in data['repos'] if not repo['fork']]
    metrics = [(data['user']['public_repos'], 'Public repositories'),
               (sum(repo['stargazers_count'] for repo in owned), 'Stars · own repositories'),
               (data['user']['followers'], 'Followers'),
               (sum(day['count'] for day in data['days']), 'Contributions · past year')]
    body = [text(34, 44, 'GITHUB / AT A GLANCE', 18, MUTED, 600, 'letter-spacing="1.5"'),
            '<path d="M320 77V342M32 212H608" stroke="#2c4051"/>']
    for i,(value,label) in enumerate(metrics):
        x,y = 34+(i%2)*304, 149+(i//2)*141
        body += [text(x,y,f'{value:,}',59,MINT if i==3 else TEXT,700),
                 text(x,y+35,label,18,MUTED)]
    body.append(text(34,389,f'Updated {data["updated"]} UTC',18,MUTED))
    return document(640,419,'Deep Golakiya — GitHub statistics',''.join(body))


def city(data):
    days = data['days']
    first = date.fromisoformat(days[0]['date'])
    offset = (first.weekday() + 1) % 7
    max_count = max(day['count'] for day in days) or 1
    total = sum(day['count'] for day in days)
    active = sum(day['count'] > 0 for day in days)
    body = [text(44, 48, 'THE CONTRIBUTION CITY', 15, MINT, 600, 'letter-spacing="2.5"'),
            text(44, 85, 'Small commits. Visible progress.', 29, TEXT, 700),
            text(1236, 48, f'{total:,} contributions', 16, TEXT, 600, 'text-anchor="end"'),
            text(1236, 74, f'{active} active days · past year', 13, MUTED, 400, 'text-anchor="end"')]
    colors = ['#172b38', '#245d57', '#349a80', '#50d5a6', MINT]
    def point(week, weekday, h=0):
        return 150 + 18.0 * week - 11 * weekday, 198 + 2.2 * week + 16 * weekday - h
    def points(values):
        return ' '.join(f'{x:.1f},{y:.1f}' for x,y in values)
    tiles = []
    for index, day in enumerate(days):
        position = index + offset
        week, weekday = divmod(position, 7)
        level = day['level']
        count = day['count']
        height = 3 if not count else 9 + 75 * math.sqrt(count / max_count)
        a = point(week, weekday)
        b = point(week + .82, weekday)
        c = point(week + .82, weekday + .82)
        d = point(week, weekday + .82)
        top = [(x, y-height) for x,y in [a,b,c,d]]
        tile = f'<g><title>{day["date"]}: {count} contributions</title>'
        tile += f'<polygon points="{points([d,c,top[2],top[3]])}" fill="{colors[level]}" opacity=".53"/>'
        tile += f'<polygon points="{points([b,c,top[2],top[1]])}" fill="{colors[level]}" opacity=".76"/>'
        tile += f'<polygon points="{points(top)}" fill="{colors[level]}" stroke="#8bf8d5" stroke-opacity="{.36 if count else .09}" stroke-width=".65"/>'
        if count:
            tile += f'<path class="motion glint" style="animation-delay:-{index%11}s" d="M{top[0][0]:.1f} {top[0][1]:.1f}L{top[1][0]:.1f} {top[1][1]:.1f}" stroke="#d3ffed" opacity=".25"/>'
        tile += '</g>'
        tiles.append((a[1], tile))
    body += [tile for _,tile in sorted(tiles, key=lambda item:item[0])]
    body += [text(44, 426, f'{days[0]["date"]} — {days[-1]["date"]}', 13, MUTED),
             text(44, 450, 'Height reflects daily contributions · dates and counts from GitHub', 12, MUTED)]
    body.append(text(1038, 426, 'LESS', 10, MUTED, 600))
    for i,color in enumerate(colors):
        body.append(f'<rect x="{1080+i*22}" y="415" width="16" height="12" rx="3" fill="{color}"/>')
    body.append(text(1236, 426, 'MORE', 10, MUTED, 600, 'text-anchor="end"'))
    body.append(text(1236, 450, f'Updated {data["updated"]} UTC', 11, MUTED, 400, 'text-anchor="end"'))
    return document(1280, 475, '3D contribution city — real GitHub activity', ''.join(body),
                    '.glint{animation:glint 5s ease-in-out infinite}@keyframes glint{50%{opacity:.95}}')


def pulse(data):
    days = data['days']
    first = date.fromisoformat(days[0]['date'])
    offset = (first.weekday()+1)%7
    x0, y0, dx, dy = 69, 103, 21.6, 17
    body = [text(42, 40, 'ACTIVITY / THE LONG GAME', 13, MINT, 600, 'letter-spacing="2"'),
            text(1238, 40, 'One square. One day.', 13, MUTED, 400, 'text-anchor="end"')]
    palette = ['#172b38','#245d57','#349a80','#50d5a6',MINT]
    for i,day in enumerate(days):
        week,weekday=divmod(i+offset,7)
        x,y=x0+week*dx,y0+weekday*dy
        body.append(f'<rect x="{x:.1f}" y="{y}" width="16" height="12" rx="3" fill="{palette[day["level"]]}"><title>{day["date"]}: {day["count"]} contributions</title></rect>')
    # A decorative, clearly labeled circuit runner, independent of the data.
    body.append('<path d="M42 69H1238" stroke="#213b4a"/>')
    body.append('<path class="motion runner" d="M42 69H1238" pathLength="100" stroke="#70bbff" stroke-width="3" stroke-linecap="round" stroke-dasharray="7 93"/>')
    body += [text(42, 256, 'PUBLIC CONTRIBUTION CALENDAR', 11, MUTED, 600, 'letter-spacing="1.4"'),
             text(1238, 256, f'Updated {data["updated"]} UTC', 11, MUTED, 400, 'text-anchor="end"')]
    return document(1280, 278, 'Contribution calendar with animated circuit line', ''.join(body),
                    '.runner{animation:run 7s linear infinite}@keyframes run{to{stroke-dashoffset:-100}}')


def city_mobile(data):
    days = data['days']
    offset = (date.fromisoformat(days[0]['date']).weekday()+1)%7
    max_count = max(d['count'] for d in days) or 1
    total = sum(d['count'] for d in days)
    colors = ['#172b38','#245d57','#349a80','#50d5a6',MINT]
    body = [text(32,43,'THE CONTRIBUTION CITY',18,MINT,600,'letter-spacing="1.8"'),
            text(32,86,'A year of progress.',35,TEXT,700),
            text(32,119,f'{total} contributions · {sum(d["count"]>0 for d in days)} active days',20,MUTED)]
    for half in range(2):
        tiles=[]
        half_days=[]
        for index,day in enumerate(days):
            week,weekday=divmod(index+offset,7)
            if week//27 != half:
                continue
            half_days.append(day)
            week %= 27
            def p(w,r,h=0):
                return 116+17.5*w-10*r,245+half*253+2*w+13*r-h
            def points(v):
                return ' '.join(f'{x:.1f},{y:.1f}' for x,y in v)
            height=3 if not day['count'] else 8+61*math.sqrt(day['count']/max_count)
            a,b,c,d=p(week,weekday),p(week+.82,weekday),p(week+.82,weekday+.82),p(week,weekday+.82)
            top=[(x,y-height) for x,y in [a,b,c,d]]
            color=colors[day['level']]
            tile=f'<g><title>{day["date"]}: {day["count"]} contributions</title>'
            tile+=f'<polygon points="{points([d,c,top[2],top[3]])}" fill="{color}" opacity=".53"/>'
            tile+=f'<polygon points="{points([b,c,top[2],top[1]])}" fill="{color}" opacity=".76"/>'
            tile+=f'<polygon points="{points(top)}" fill="{color}" stroke="#8bf8d5" stroke-opacity=".16" stroke-width=".6"/></g>'
            tiles.append((a[1],tile))
        if half_days:
            body.append(text(32,157+half*253,f'{half_days[0]["date"]} — {half_days[-1]["date"]}',17,MUTED))
            body += [tile for _,tile in sorted(tiles,key=lambda item:item[0])]
    body += [text(32,686,'Taller columns = more contributions',18,MUTED),
             text(32,716,f'Updated {data["updated"]} UTC',17,MUTED)]
    return document(640,744,'3D contribution city — real GitHub activity',''.join(body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', default='deepbajud')
    parser.add_argument('--from-cache', type=Path, help='Development only: captured public API responses')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', args.username):
        parser.error('Invalid GitHub username')
    data = collect(args.username, args.from_cache)
    # Collect and render everything before replacing any working assets.
    outputs = {'stats.svg': stats(data), 'stats-mobile.svg': stats_mobile(data), 'contribution-city.svg': city(data),
               'contribution-pulse.svg': pulse(data), 'contribution-city-mobile.svg': city_mobile(data)}
    ASSETS.mkdir(exist_ok=True)
    for name, value in outputs.items():
        target = ASSETS / name
        temp = target.with_suffix('.svg.tmp')
        temp.write_text(value, encoding='utf-8')
        temp.replace(target)
    print(f'Updated {len(outputs)} SVGs for {args.username}; {sum(d["count"] for d in data["days"])} contributions in calendar.')


if __name__ == '__main__':
    main()
