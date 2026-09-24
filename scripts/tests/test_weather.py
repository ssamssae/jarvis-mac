from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_weather import weather_reply, fetch_weather

CONFIG={'name':'테스트 지역','aliases':['테스트'],'latitude':37.5,'longitude':127,'timezone':'Asia/Seoul'}
NOW=datetime(2026,9,24,12,tzinfo=ZoneInfo('Asia/Seoul'))
DATA={'daily':{'time':['2026-09-24','2026-09-25'],'temperature_2m_min':[16,17],'temperature_2m_max':[24,25],'precipitation_probability_max':[10,60]},'daily_units':{'temperature_2m_min':'°C','temperature_2m_max':'°C','precipitation_probability_max':'%'},'current':{'time':'2026-09-24T12:00','temperature_2m':22.5},'current_units':{'temperature_2m':'°C'}}

class WeatherTests(unittest.TestCase):
    def test_today_and_tomorrow_use_distinct_dated_values(self):
        for phrase in ['오늘 날씨','날씨 어때?','테스트 지금 날씨 알려줘','오늘 비 와?']:
            result=weather_reply(phrase,CONFIG,lambda c:DATA,NOW)
            self.assertEqual(result['status'],'ok',phrase)
            self.assertIn('22.5도',result['answer']);self.assertIn('10퍼센트',result['answer'])
        result=weather_reply('내일 날씨',CONFIG,lambda c:DATA,NOW)
        self.assertIn('25도',result['answer']);self.assertIn('60퍼센트',result['answer'])
        self.assertNotIn('22.5',result['answer']);self.assertEqual(result['sources'][0]['forecast_date'],'2026-09-25')

    def test_unconfigured_wrong_region_and_unsupported_time_do_not_fetch(self):
        fetch=Mock()
        self.assertEqual(weather_reply('오늘 날씨',None,fetch,NOW)['status'],'needs_location')
        for phrase in ['다른도시 날씨','다음주 날씨','어제 날씨','오늘 날씨 알려주고 불 켜줘']:
            self.assertEqual(weather_reply(phrase,CONFIG,fetch,NOW)['status'],'unsupported')
        self.assertIsNone(weather_reply('선풍기 켜줘',CONFIG,fetch,NOW))
        fetch.assert_not_called()

    def test_missing_stale_wrong_units_and_nonfinite_data_fail_closed(self):
        cases=[]
        for key,value in [('time','2026-09-23T12:00'),('temperature_2m',None),('temperature_2m',float('nan'))]:
            d=deepcopy(DATA);d['current'][key]=value;cases.append(d)
        d=deepcopy(DATA);d['daily_units']['temperature_2m_max']='°F';cases.append(d)
        d=deepcopy(DATA);d['daily']['time']=['2020-01-01'];cases.append(d)
        d=deepcopy(DATA);d['daily']['precipitation_probability_max'][0]=None;cases.append(d)
        for data in cases:
            result=weather_reply('날씨',CONFIG,lambda c:data,NOW)
            self.assertEqual(result['status'],'unavailable');self.assertEqual(result['sources'],[])
        result=weather_reply('날씨',CONFIG,Mock(side_effect=TimeoutError()),NOW)
        self.assertEqual(result['status'],'unavailable')

    def test_http_response_limit_and_coordinates(self):
        response=MagicMock();response.read.return_value=b'x'*64001
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch('jarvis_weather.urllib.request.build_opener',return_value=opener):
            with self.assertRaisesRegex(ValueError,'too_large'):fetch_weather(CONFIG)
        with self.assertRaisesRegex(ValueError,'coordinates'):fetch_weather(dict(CONFIG,latitude=999))

    def test_controller_weather_never_calls_cursor_or_devices(self):
        import io,json,tempfile,time
        import jarvis_mac_listener as app
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/'audio';audio.mkdir();clip=audio/'one.wav';clip.write_bytes(b'x'*44)
            (root/'config.json').write_text(json.dumps({'cast_name':'fixture','model':'fixture','weather':CONFIG}))
            now=time.time();event={'wav':str(clip),'speech_started_wall':now-2,'speech_ended_wall':now-1,'capture_ended_wall':now}
            stt=MagicMock();stt.read.return_value={'text':'자비스 오늘 날씨'}
            home=MagicMock();home.plan.return_value=None
            speech=MagicMock();speech.first_playing=None;speech.ack_first_playing=None;speech.events=[]
            reply=weather_reply('오늘 날씨',CONFIG,lambda c:DATA,NOW)
            with patch.object(sys,'argv',['listener','--state-dir',str(root)]),patch.object(sys,'stdin',io.StringIO(json.dumps(event)+'\n')),patch.object(sys,'stdout',io.StringIO()),patch.object(app.signal,'signal'),patch.object(app,'SmartHome',return_value=home),patch.object(app,'JSONWorker',return_value=stt),patch.object(app,'CastOutput'),patch.object(app,'SpeechQueue',return_value=speech),patch.object(app,'CursorQA') as qa,patch.object(app,'weather_reply',return_value=reply):
                app.main()
            qa.assert_not_called();home.execute.assert_not_called();speech.submit.assert_called_once_with(reply['answer'])
            receipt=json.loads((root/'last-turn.json').read_text());self.assertEqual(receipt['weather']['status'],'ok')
