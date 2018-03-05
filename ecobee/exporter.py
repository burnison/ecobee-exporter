import argparse
import httplib2
import json
import os
import pickle
import socket
import struct
import pytz

from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from functools import partial
from graphitesend import GraphiteClient
from oauth2client import tools
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.file import Storage
from urllib.parse import quote

API_HOST = 'https://api.ecobee.com'
API_URL = "%s/1" % API_HOST

EXPORTER_HOME = "%s/.ecobee-exporter" % os.environ['HOME']
CREDENDIAL_STORE = "%s/client_secrets.json" % EXPORTER_HOME

CLIENT_ID = os.environ['CLIENT_ID']

UTF_8 = 'utf-8'

class EcobeeError(Exception):
    def __init__(self, msg):
        super().__init__(msg)

class Ecobee:
    def __init__(self, args):
        self.http = httplib2.Http()
        self.__setup(args)
        self.__authenticate(args).authorize(self.http)
        self.start = datetime.strptime(args.date, '%Y-%m-%d')
        self.end = self.start + timedelta(days=args.days)
        self.columns = args.columns
        self.selector = args.selector

    def __setup(self, args):
        if not os.path.exists(EXPORTER_HOME):
            os.mkdir(EXPORTER_HOME)
        if not os.path.exists(CREDENDIAL_STORE):
            open(CREDENDIAL_STORE, 'w+').close()
        return EXPORTER_HOME, CREDENDIAL_STORE

    def __authenticate(self, args):
        flow = OAuth2WebServerFlow(
                auth_uri='%s/authorize' % API_HOST,
                token_uri='%s/token' % API_HOST,
                client_id=CLIENT_ID,
                scope='smartRead',
                user_agent='ecobee-exporter/1.1')
        storage = Storage(CREDENDIAL_STORE)

        credz = storage.get()

        if credz is None or credz.invalid:
            credz = tools.run_flow(flow, storage, args)

        elif credz is not None and credz.access_token_expired:
            credz.refresh(httplib2.Http())

        return credz

    def runtime(self):
        selector = {
            'selection': {
                'selectionType': 'thermostats',
                'selectionMatch': self.selector,
            },
            'startDate': self.start.strftime('%Y-%m-%d'),
            'endDate': self.end.strftime('%Y-%m-%d'),
            'columns': self.columns,
            'includeSensors': True,
        }
        resp, content = self.http.request("%s/runtimeReport?format=json&body=%s&_timestamp=%s" % (
                API_URL,
                quote(json.dumps(selector)),
                datetime.now().strftime('%s')
            ), headers={
                'Content-Type': 'application/json;charset=UTF-8',
            }
        )
        if resp.status == 200:
            content = content.decode(UTF_8)
            return json.loads(content)
        else:
            raise EcobeeError("Unable to get runtime report: %s\n%s" %
                    (resp, content))


class Graphite:
    def __init__(self, connect, prefix, tags):
        self.prefix = prefix
        self.connection = connect

    def __prefix(self, metric):
        return "%s.%s" % (self.prefix, metric[0])

    def send(self, metrics):
        prefixed = [(self.__prefix(t), (t[1][0], t[1][1])) for t in metrics]
        payload = pickle.dumps(prefixed, protocol=2)

        s = socket.socket()
        s.connect(self.connection)
        s.sendall(struct.pack('!L', len(payload)))
        s.sendall(payload)
        s.close()

class Influx:
    def __init__(self, connect, prefix, tags):
        self.prefix = prefix
        self.connection = connect
        self.http = httplib2.Http()
        self.tags = tags

    def __prefix(self, metric):
        return "%s.%s" % (self.prefix, metric[0])

    def send(self, metrics, extra_tags=[]):
        tags = ",".join(self.tags + extra_tags)
        lines = [
            "%s,%s value=%s %d" % (self.__prefix(t), tags if len(t[1]) == 2 else ','.join(t[1][2] + [tags]), t[1][1], t[1][0] * 1000 * 1000000)
            for t in metrics ]
        payload = "\n".join(lines)

        host = "%s:%s" % (self.connection[0], self.connection[1])
        uri = "http://%s/write?db=graphite" % host

        resp, content = self.http.request(uri, method="POST", body=payload)

        if resp.status != 204:
            raise EcobeeError(
                "Unable to send InfluxDB data: %s\n%s" % (resp, content)
            )

class Decoders:
    drop = lambda x: None
    ftoc = lambda x: (float(x) - 32.0) * (5 / 9)
    percentage = lambda x: float(x) / 100.0
    passthrough = lambda x: float(x)
    binary = lambda x: 1 if int(x) == 1 == True else 0

    runtime_decoders = {
        'zoneHVACmode': drop,           #heatOff
        'zoneCalendarEvent': drop,      #
        'zoneCoolTemp': ftoc,           #74.3
        'zoneHeatTemp': ftoc,           #69.8
        'zoneAveTemp': ftoc,            #70
        'zoneHumidity': percentage,     #43
        'zoneHumidityLow': percentage,     #43
        'zoneHumidityHigh': percentage,     #43
        'zoneOccupancy': binary,
        'outdoorTemp': ftoc,            #50
        'outdoorHumidity': percentage,  #46
        'compCool1': passthrough,       #0
        'compCool2': passthrough,       #0
        'compHeat1': passthrough,       #0
        'compHeat2': passthrough,       #0
        'auxHeat1': passthrough,        #0
        'auxHeat2': passthrough,        #0
        'auxHeat3': passthrough,        #0
        'fan': passthrough,             #0
        'humidifier': passthrough,      #0
        'dehumidifier': passthrough,    #0
        'economizer': passthrough,      #0
        'ventilator': passthrough,      #0
        'HVACmode': drop,               #
        'zoneClimate': drop,            #Home
        'wind': passthrough,            #33
        'sky': passthrough,
    }

    sensor_decoders = {
        'temperature': ftoc,
        'humidity': passthrough,
        'occupancy': binary,
    }

    @classmethod
    def decode_runtime(clz, key, value):
        if value == 'null':
            return None
        return clz.runtime_decoders[key](value)

    @classmethod
    def decode_sensor(clz, sensor_type, value):
        if value == 'null':
            return None
        return clz.sensor_decoders[sensor_type](value)

def _stream_to(reports, ingester):
    timezone = pytz.timezone("America/Toronto")
    headers = reports['columns'].split(',')

    metrics = []

    for report in reports['reportList']:
        thermostat = report['thermostatIdentifier']

        for row in report['rowList']:
            parts = row.split(',', 2)
            instant = datetime.strptime("%s %s" % tuple(parts[0:2]), '%Y-%m-%d %H:%M:%S')
            instant = timezone.localize(instant).astimezone(pytz.utc)
            instant = int(instant.strftime('%s'))


            # There's an extra column that shouldn't be there. Weird.
            for i, raw_reading in enumerate(parts[2].split(',')[:-1]):
                if raw_reading == '':
                    continue
                reading_type = headers[i]
                reading = Decoders.decode_runtime(reading_type, raw_reading)
                if reading is not None:
                    metrics.append((reading_type, (instant, reading)))


    for sensors in reports['sensorList']:
        thermostat = sensors['thermostatIdentifier']
        sensor_map = {}
        for sensor in sensors['sensors']:
            sensor_map[sensor['sensorId']] = {
                'id': sensor['sensorId'],
                'name': sensor['sensorName'],
                'type': sensor['sensorType'],
            }

        headers = sensors['columns'][2:]
        for row in sensors['data']:
            parts = row.split(',', 2)
            instant = datetime.strptime("%s %s" % tuple(parts[0:2]), '%Y-%m-%d %H:%M:%S')
            instant = timezone.localize(instant).astimezone(pytz.utc)
            instant = int(instant.strftime('%s'))

            # There's an extra column that shouldn't be there. Weird.
            for i, raw_reading in enumerate(parts[2].split(',')[:-1]):
                if raw_reading == '':
                    continue
                sensor_data = sensor_map[headers[i]]
                sensor_type = sensor_data['type']
                reading = Decoders.decode_sensor(sensor_type, raw_reading)
                if reading is not None:
                    name = sensor_data['name']
                    metrics.append((
                        'sensor.%s' % (sensor_data['id']),
                        (instant, reading, [
                            "name=%s" % name.lower().replace(' ', '_'),
                            "type=%s" % sensor_type,
                        ]))
                    )

        ingester(metrics, extra_tags=["thermostat=%s" % thermostat])


def main(argv=None):
    parser = argparse.ArgumentParser(parents=[tools.argparser])

    ingester_group = parser.add_mutually_exclusive_group(required=True)
    ingester_group.add_argument('--graphite', metavar='url',
            help='Use a graphite host, for example, localhost:2004.')
    ingester_group.add_argument('--influx', metavar='url',
            help='Use an influx host, for example, localhost:8086.')

    parser.add_argument('-t', '--tag', required=False, default=[],
            metavar='key=value', action='append',
            help='A set of tags for the storage system, if supported. This argument may be repeated.')

    parser.add_argument('--debug', action='store_true', default=False)
    parser.add_argument('--days', default='1', type=int, required=False, help='Number of days. Default is 1.')
    parser.add_argument('--columns', help='CSV list of report columns.', default='zoneHvacMode,zoneCalendarEvent,zoneCoolTemp,zoneHeatTemp,zoneAveTemp,zoneHumidity,zoneHumidityLow,zoneHumidityHigh,zoneOccupancy,outdoorTemp,outdoorHumidity,wind,sky,compCool1,compCool2,compHeat1,compHeat2,auxHeat1,auxHeat2,auxHeat3,fan,humidifier,dehumidifier,economizer,ventilator,hvacMode,zoneClimate',)
    parser.add_argument('date', help='The start date to export in the format of YYYY-mm-dd, or \'today\'.')
    parser.add_argument('selector', help='Thermostat selector. Likely a serial number.')
    args = parser.parse_args()

    if args.debug:
        httplib2.debuglevel=4

    ingester_clazz = Graphite if args.graphite else Influx
    connection = (args.graphite or args.influx).split(':')
    ingester = ingester_clazz((connection[0], int(connection[1])), 'ecobee.thermostat', args.tag).send

    _stream_to(Ecobee(args).runtime(), ingester)


if __name__ == "__main__":
    main()
