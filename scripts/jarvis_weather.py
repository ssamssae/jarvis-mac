"""Bounded live weather lookup; locality is private opt-in configuration."""
from datetime import datetime, timedelta
import json
import math
import re
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = 'https://api.open-meteo.com/v1/forecast'
DOCS = 'https://open-meteo.com/en/docs'


def fetch_weather(config):
    lat, lon = float(config['latitude']), float(config['longitude'])
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('invalid_coordinates')
    query = urllib.parse.urlencode({'latitude':lat, 'longitude':lon,
        'current':'temperature_2m',
        'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
        'timezone':config.get('timezone', 'Asia/Seoul'), 'forecast_days':2})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('weather_redirect_rejected')
    with urllib.request.build_opener(NoRedirect).open(API+'?'+query, timeout=6) as response:
        raw = response.read(64001)
    if len(raw) > 64000:
        raise ValueError('weather_response_too_large')
    return json.loads(raw)


def number(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('invalid_weather_number')
    return f'{value:g}'


def weather_reply(question, config, fetch=fetch_weather, now=None):
    key = re.sub(r'[\s,.!?，。！？]', '', question)
    if '날씨' not in key and not re.search(r'(?:오늘|내일|지금)?비(?:와|오니|올까|오나요)', key):
        return None
    base = {'intent':'weather', 'sources':[]}
    if not config or not config.get('name'):
        return dict(base, status='needs_location', answer='날씨를 확인할 기본 지역이 없어요. 먼저 시나 구를 설정해 주세요.')
    # Never silently answer a different locality, time range, or a compound request.
    aliases = sorted({config['name'], *config.get('aliases', [])}, key=len, reverse=True)
    for alias in aliases:
        normalized = re.sub(r'\s', '', alias)
        if normalized and key.startswith(normalized):
            key = key[len(normalized):]
            break
    match = re.fullmatch(r'(오늘|내일|지금|현재)?(?:날씨(?:는|가)?(?:어때|어때요|어떻게돼|알려줘|알려주세요|좀알려줘|알려줄래|어떤가요)?|비(?:와|오니|올까|오나요))', key)
    if not match:
        return dict(base, status='unsupported', answer=f'현재는 {config["name"]}의 오늘과 내일 날씨를 확인할 수 있어요.')
    day = 1 if match[1] == '내일' else 0
    try:
        zone = ZoneInfo(config.get('timezone', 'Asia/Seoul'))
        clock = now or datetime.now(zone)
        clock = clock.astimezone(zone)
        data = fetch(config)
        daily, units = data['daily'], data['daily_units']
        target = (clock.date()+timedelta(days=day)).isoformat()
        index = daily['time'].index(target)
        if units['temperature_2m_max'] != '°C' or units['temperature_2m_min'] != '°C' or units['precipitation_probability_max'] != '%':
            raise ValueError('unexpected_weather_units')
        low = number(daily['temperature_2m_min'][index], -100, 70)
        high = number(daily['temperature_2m_max'][index], -100, 70)
        rain = number(daily['precipitation_probability_max'][index], 0, 100)
        if float(low) > float(high):
            raise ValueError('invalid_temperature_range')
        answer = f'{config["name"]} {"내일" if day else "오늘"} 예보는 최저 {low}도, 최고 {high}도, 강수 확률 {rain}퍼센트예요.'
        if not day:
            current = data['current']
            stamp = datetime.fromisoformat(current['time']).replace(tzinfo=zone)
            age = (clock-stamp).total_seconds()
            if not -1800 <= age <= 7200 or data['current_units']['temperature_2m'] != '°C':
                raise ValueError('stale_current_weather')
            temp = number(current['temperature_2m'], -100, 70)
            answer = f'{config["name"]} 현재 기온은 {temp}도예요. ' + answer
        return dict(base, status='ok', answer=answer,
                    sources=[{'url':DOCS, 'provider':'Open-Meteo', 'fetched_at':time.time(), 'forecast_date':target}])
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return dict(base, status='unavailable', answer='지금 최신 날씨 정보를 가져오지 못했어요. 잠시 후 다시 물어봐 주세요.')
