ecobee-exporter
===============

A crude, as-is tool that can export Ecobee data to Graphite or InfluxDB. To use
this application, you will have to create a new Ecobee application under your
account using the web flow.

Usage
=====

`ecobee-exporter` assumes you have a `client_id` for your application and
expects them to be set into environmental variables. Upon first run,
`ecobee-exporter` will go through the OAuth flow and allow this application
access to your account.

Because ecobee has weird URL validation on their OAuth2 return path, you'll
likely have to modify your hosts file and create a FQDN that points to 127.0.0.1
that passes validation for the `--auth_host_name`. I use `example.com`.

```
export CLIENT_ID=1

./ecobee-exporter
usage: exporter.py [-h] [--auth_host_name AUTH_HOST_NAME]
                   [--noauth_local_webserver]
                   [--auth_host_port [AUTH_HOST_PORT [AUTH_HOST_PORT ...]]]
                   [--logging_level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                   (--graphite url | --influx url) [-t key=value] [--debug]
                   [--days DAYS] [--columns COLUMNS]
                   date selector

positional arguments:
  date                  The start date to export in the format of YYYY-mm-dd,
                        or 'today'.
  selector              Thermostat selector. Likely a serial number.

optional arguments:
  -h, --help            show this help message and exit
  --auth_host_name AUTH_HOST_NAME
                        Hostname when running a local web server.
  --noauth_local_webserver
                        Do not run a local web server.
  --auth_host_port [AUTH_HOST_PORT [AUTH_HOST_PORT ...]]
                        Port web server should listen on.
  --logging_level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                        Set the logging level of detail.
  --graphite url        Use a graphite host, for example, localhost:2004.
  --influx url          Use an influx host, for example, localhost:8086.
  -t key=value, --tag key=value
                        A set of tags for the storage system, if supported. This argument may be repeated.
  --debug
  --days days           The number of days to export from the start date.
  --columns             A CSV list of runtime report columns.
```
