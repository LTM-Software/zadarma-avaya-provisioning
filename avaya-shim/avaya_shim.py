#!/usr/bin/env python3
import functools
import html
import http.server
import json
import os
from pathlib import Path
import platform
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET


HTTP_ROOT = os.environ.get("HTTP_ROOT", "/srv/http")
APP_ROOT = os.environ.get("APP_ROOT", str(Path(HTTP_ROOT).resolve().parent))
ENV_PATH = os.environ.get("AVAYA_ENV_PATH", str(Path(APP_ROOT) / ".env"))
LOG_DIR = os.environ.get("AVAYA_LOG_DIR", str(Path(APP_ROOT) / "logs"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))
SIP_LISTEN = ("0.0.0.0", int(os.environ.get("SIP_PORT", "5060")))
SIP_REMOTE = (
    os.environ.get("SIP_REMOTE_HOST", "185.45.152.164"),
    int(os.environ.get("SIP_REMOTE_PORT", "5060")),
)
SIP_ADVERTISE_HOST = os.environ.get("SIP_ADVERTISE_HOST", "192.168.80.10")
SIP_ADVERTISE_PORT = os.environ.get("SIP_ADVERTISE_PORT", str(SIP_LISTEN[1]))
SIP_ADVERTISE_HOSTPORT = f"{SIP_ADVERTISE_HOST}:{SIP_ADVERTISE_PORT}"
SIP_EXPIRES_DEFAULT = os.environ.get("SIP_EXPIRES", "120")
SIP_INVITE_EXPIRES_DEFAULT = os.environ.get("SIP_INVITE_EXPIRES", "180")
EXTENSION = os.environ.get("AVAYA_EXTENSION", "373316-100")
DOMAIN = os.environ.get("AVAYA_DOMAIN", "pbx.zadarma.com")
LOGO_LABEL = os.environ.get("AVAYA_LOGO_LABEL", "LTM")
LOGO_URL = os.environ.get(
    "AVAYA_LOGO_URL",
    f"http://{SIP_ADVERTISE_HOST}/ltm-logo-232x140.jpg",
)
ENABLE_HTTP = os.environ.get("ENABLE_HTTP", "1").lower() not in {"0", "false", "no"}
ENABLE_SIP = os.environ.get("ENABLE_SIP", "1").lower() not in {"0", "false", "no"}
AVAYA_FNU_INVITE_RESPONSE = os.environ.get("AVAYA_FNU_INVITE_RESPONSE", "183")
dialog_routes = {}
local_fnu_invites = {}
register_call_routes = {}
state_lock = threading.RLock()
runtime_events = []
phone_registry = {}
START_TIME = time.time()


def log(message):
    with state_lock:
        runtime_events.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message})
        del runtime_events[:-300]
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def soap_envelope(body):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                  xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/">
  <soapenv:Body>
{body}
  </soapenv:Body>
</soapenv:Envelope>
"""


def emergency_numbers():
    return """    <ListOfEmergencyNumbers>
      <NoOfElements>1</NoOfElements>
      <EmergencyNumberList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                           soapenc:arrayType="ns1:EmergencyNumberData[1]"
                           xsi:type="soapenc:Array">
        <item>
          <Type>Emergency</Type>
          <Number>911</Number>
        </item>
      </EmergencyNumberList>
    </ListOfEmergencyNumbers>"""


def ip_family():
    return """    <IpAddressFamilySettings>
      <SignalingMode>4</SignalingMode>
      <MediaMode>4</MediaMode>
    </IpAddressFamilySettings>"""


def button_assignments():
    items = []
    for location in range(1, 4):
        items.append(
            f"""        <item>
          <Location>{location}</Location>
          <ButtonType>call-appr</ButtonType>
          <Label>{EXTENSION}</Label>
          <LineID>{location}</LineID>
          <Address />
          <FNUType />
          <App>false</App>
          <Media>false</Media>
          <FNUInfo soapenc:arrayType="ns1:FNUData[0]" xsi:type="soapenc:Array" />
        </item>"""
        )
    return f"""    <ListOfButtonAssignments>
      <NoOfElements>3</NoOfElements>
      <ButtonAssignment xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                        soapenc:arrayType="ns1:ButtonData[3]"
                        xsi:type="soapenc:Array">
{os.linesep.join(items)}
      </ButtonAssignment>
    </ListOfButtonAssignments>"""


def dial_plan():
    patterns = ["#", "*xx", "1xx", "xxx", "xxxx", "xxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxxx"]
    items = "\n".join(f"        <item>{pattern}</item>" for pattern in patterns)
    return f"""    <DialPlanData>
      <DialPlanDomain>{DOMAIN}</DialPlanDomain>
      <NoOfElements>{len(patterns)}</NoOfElements>
      <DialPlan xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                soapenc:arrayType="xsd:string[{len(patterns)}]"
                xsi:type="soapenc:Array">
{items}
      </DialPlan>
    </DialPlanData>"""


def timers():
    return """    <ListOfTimers>
      <NoOfElements>3</NoOfElements>
      <TimerList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 soapenc:arrayType="ns1:Timer[3]"
                 xsi:type="soapenc:Array">
        <item>
          <Precision>seconds</Precision>
          <TimerName>registration</TimerName>
          <TimerValue>120</TimerValue>
        </item>
        <item>
          <Precision>seconds</Precision>
          <TimerName>line-reservation</TimerName>
          <TimerValue>30</TimerValue>
        </item>
        <item>
          <Precision>seconds</Precision>
          <TimerName>subscription</TimerName>
          <TimerValue>3600</TimerValue>
        </item>
      </TimerList>
    </ListOfTimers>"""


def maintenance_data():
    pairs = {
        "QKLOGINSTAT": "0",
        "RECOVERYREGISTERWAIT": "60",
        "FAILBACK_POLICY": "auto",
        "FAST_RESPONSE_TIMEOUT": "2",
        "SIPREGPROXYPOLICY": "alternate",
        "CALL_CONTROL_802_PRIORITY": "6",
        "AUDIO_802_PRIORITY": "6",
        "LOGOS": f"{LOGO_LABEL}={LOGO_URL}",
        "CURRENT_LOGO": LOGO_LABEL,
    }
    items = "\n".join(
        f"""        <item>
          <MDName>{name}</MDName>
          <MDValue>{value}</MDValue>
        </item>"""
        for name, value in pairs.items()
    )
    return f"""    <ListOfMaintenanceData>
      <NoOfElements>{len(pairs)}</NoOfElements>
      <MaintenanceDataList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                           soapenc:arrayType="ns1:MaintenanceData[{len(pairs)}]"
                           xsi:type="soapenc:Array">
{items}
      </MaintenanceDataList>
    </ListOfMaintenanceData>"""


def identities():
    return f"""    <ListOfIdentities>
      <NoOfElements>1</NoOfElements>
      <IdentityList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                    soapenc:arrayType="ns1:Identity[1]"
                    xsi:type="soapenc:Array">
        <item>
          <Address>{EXTENSION}</Address>
          <Type>shortform</Type>
        </item>
      </IdentityList>
    </ListOfIdentities>"""


def all_endpoint_config():
    return f"""  <ConfigInfo>
    <VolumeSettings>
      <RingerVolume>5</RingerVolume>
      <ReceiverVolume>5</ReceiverVolume>
      <SpeakerVolume>5</SpeakerVolume>
      <RingerCadence>3</RingerCadence>
    </VolumeSettings>
{timers()}
    <LinePreferenceInfo>
      <callAppPreference>n</callAppPreference>
      <bridgeAppPreference>n</bridgeAppPreference>
    </LinePreferenceInfo>
    <MWExt />
    <AutoAnswer>none</AutoAnswer>
{button_assignments()}
{dial_plan()}
    <VMONInfo>
      <RtcpServer />
      <VmonPort>5005</VmonPort>
      <ReportPeriod>5</ReportPeriod>
    </VMONInfo>
    <VideoInfo>
      <IPSoftphoneEnable>false</IPSoftphoneEnable>
      <IPVideoEnable>false</IPVideoEnable>
    </VideoInfo>
{maintenance_data()}
{identities()}
    <VMNumber />
{emergency_numbers()}
    <MuteOnRemoteOffHook>n</MuteOnRemoteOffHook>
    <ListOfCmSystemParameters>
      <NoOfElements>0</NoOfElements>
      <CmSystemParameters xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                          soapenc:arrayType="ns1:CmSystemParameter[0]"
                          xsi:type="soapenc:Array" />
    </ListOfCmSystemParameters>
{ip_family()}
    <TerminalGroupId>0</TerminalGroupId>
  </ConfigInfo>"""


def initial_endpoint_response():
    body = f"""<ns1:getInitialEndpointConfigurationResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <ConfigInfo>
{emergency_numbers()}
{maintenance_data()}
{ip_family()}
  </ConfigInfo>
</ns1:getInitialEndpointConfigurationResponse>"""
    return soap_envelope(body)


def all_endpoint_response():
    body = f"""<ns1:getAllEndpointConfigurationResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
{all_endpoint_config()}
</ns1:getAllEndpointConfigurationResponse>"""
    return soap_envelope(body)


def empty_list_response(method, list_name="List", array_name="Items"):
    body = f"""<ns1:{method}Response
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <{list_name}>
    <NoOfElements>0</NoOfElements>
    <{array_name} xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 soapenc:arrayType="xsd:string[0]"
                 xsi:type="soapenc:Array" />
  </{list_name}>
</ns1:{method}Response>"""
    return soap_envelope(body)


def ok_response(method):
    body = f"""<ns1:{method}Response
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <ReturnCode>0</ReturnCode>
</ns1:{method}Response>"""
    return soap_envelope(body)


def ppm_success_response(method):
    body = f"""<ns1:{method}Response
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <PPMResponse>PPM_Success</PPMResponse>
</ns1:{method}Response>"""
    return soap_envelope(body)


def call_history_response():
    body = """<ns1:getCallHistoryResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <CallHistoryList>
    <NoOfElements>0</NoOfElements>
    <CallHistoryInfo xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     soapenc:arrayType="ns1:CallHistoryData[0]"
                     xsi:type="soapenc:Array" />
  </CallHistoryList>
</ns1:getCallHistoryResponse>"""
    return soap_envelope(body)


def device_data_response():
    config_data = f"""<ConfigData xmlns="http://xml.avaya.com/endpointAPI">
<version>{int(time.time())}</version>
<parameter>
<name>CurrentLogo</name>
<alias/>
<value>{html.escape(LOGO_LABEL)}</value>
<category>Config</category>
</parameter>
<parameter>
<name>AlwaysPromptForUsernameAndPassword</name>
<alias/>
<value>0</value>
<category>Config</category>
</parameter>
</ConfigData>"""
    body = f"""<ns1:getDeviceDataResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <Identity>
    <DeviceType>one-X Communicator</DeviceType>
    <DeviceVendor>Avaya</DeviceVendor>
    <DeviceModel>96x1</DeviceModel>
  </Identity>
  <NoOfElements>1</NoOfElements>
  <DeviceDataList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  soapenc:arrayType="ns1:DeviceData[1]"
                  xsi:type="soapenc:Array">
    <item>
      <DataCategory>Config</DataCategory>
      <DataName>XML</DataName>
      <DataValue>{html.escape(config_data)}</DataValue>
    </item>
  </DeviceDataList>
</ns1:getDeviceDataResponse>"""
    return soap_envelope(body)


def ppm_url():
    return f"http://{SIP_ADVERTISE_HOST}/axis/services/PPM"


def home_server_response():
    body = f"""<ns1:getHomeServerResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <ServerInfo>
    <ppmServer>{ppm_url()}</ppmServer>
    <sipServer>{SIP_ADVERTISE_HOST}</sipServer>
    <sipDomain>{DOMAIN}</sipDomain>
    <transportDataInfo>
      <NoOfElements>1</NoOfElements>
      <TransportList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     soapenc:arrayType="ns1:TransportData[1]"
                     xsi:type="soapenc:Array">
        <item>
          <transportName>UDP</transportName>
          <transportPort>{SIP_ADVERTISE_PORT}</transportPort>
        </item>
      </TransportList>
    </transportDataInfo>
  </ServerInfo>
</ns1:getHomeServerResponse>"""
    return soap_envelope(body)


def home_capabilities_response():
    body = f"""<ns1:getHomeCapabilitiesResponse
    xmlns:ns1="http://xml.avaya.com/service/ProfileManagement/112004"
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <ServerCapabilities>
    <ServicesList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  soapenc:arrayType="ns1:ServiceData[3]"
                  xsi:type="soapenc:Array">
      <item>
        <ServiceName>endpoint-reflection</ServiceName>
        <ServiceURI>{SIP_ADVERTISE_HOST}</ServiceURI>
        <NoOfTransportElements>0</NoOfTransportElements>
        <ServiceVersion>1</ServiceVersion>
        <ServiceFeatures soapenc:arrayType="ns1:FeatureData[0]" xsi:type="soapenc:Array" />
        <NoOfFeatureElements>0</NoOfFeatureElements>
      </item>
      <item>
        <ServiceName>ppm-features</ServiceName>
        <ServiceVersion>1</ServiceVersion>
        <ServiceFeatures soapenc:arrayType="ns1:FeatureData[3]" xsi:type="soapenc:Array">
          <item>
            <FeatureName>FS-DeviceData</FeatureName>
            <FeatureVersion>2</FeatureVersion>
            <FeatureValue>FS-Available</FeatureValue>
          </item>
          <item>
            <FeatureName>setVolumeSettings</FeatureName>
            <FeatureVersion>1</FeatureVersion>
            <FeatureValue>Method-Available</FeatureValue>
          </item>
          <item>
            <FeatureName>getAllEndpointConfiguration</FeatureName>
            <FeatureVersion>1</FeatureVersion>
            <FeatureValue>Method-Available</FeatureValue>
          </item>
        </ServiceFeatures>
        <NoOfFeatureElements>3</NoOfFeatureElements>
      </item>
      <item>
        <ServiceName>proxy-server</ServiceName>
        <ServiceFQDN>{SIP_ADVERTISE_HOST}</ServiceFQDN>
        <ServiceDomain>{DOMAIN}</ServiceDomain>
        <PPMServiceFQDN>{ppm_url()}</PPMServiceFQDN>
        <ServiceType>CoreSM</ServiceType>
        <ServiceURI>{SIP_ADVERTISE_HOST}</ServiceURI>
        <PPMServiceURI>{ppm_url()}</PPMServiceURI>
        <ServiceTransport soapenc:arrayType="ns1:TransportData[1]" xsi:type="soapenc:Array">
          <item>
            <transportName>UDP</transportName>
            <transportPort>{SIP_ADVERTISE_PORT}</transportPort>
          </item>
        </ServiceTransport>
        <NoOfTransportElements>1</NoOfTransportElements>
        <ServiceVersion>1</ServiceVersion>
        <ServiceFeatures soapenc:arrayType="ns1:FeatureData[3]" xsi:type="soapenc:Array">
          <item>
            <FeatureName>FS-AST</FeatureName>
            <FeatureVersion>0</FeatureVersion>
            <FeatureValue>FS-Available</FeatureValue>
          </item>
          <item>
            <FeatureName>FS-PPM</FeatureName>
            <FeatureVersion>0</FeatureVersion>
            <FeatureValue>FS-Available</FeatureValue>
          </item>
          <item>
            <FeatureName>servicePriority</FeatureName>
            <FeatureVersion>0</FeatureVersion>
            <FeatureValue>1</FeatureValue>
          </item>
        </ServiceFeatures>
        <NoOfFeatureElements>3</NoOfFeatureElements>
      </item>
    </ServicesList>
    <NoOfServiceElements>3</NoOfServiceElements>
    <FNUList xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/"
             xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
             soapenc:arrayType="ns1:FNUFeature[0]"
             xsi:type="soapenc:Array" />
    <NoOfFNUElements>0</NoOfFNUElements>
  </ServerCapabilities>
</ns1:getHomeCapabilitiesResponse>"""
    return soap_envelope(body)


def ppm_method(payload):
    methods = (
        "getInitialEndpointConfiguration",
        "getAllEndpointConfiguration",
        "getDeviceData",
        "setDeviceData",
        "getHomeCapabilities",
        "getHomeServer",
        "getPermissionsType",
        "setVolumeSettings",
        "setOneTouchDialList",
        "getContactList",
        "addContact",
        "getCallHistory",
        "deleteCallHistory",
    )
    for method in methods:
        if method in payload:
            return method

    try:
        root = ET.fromstring(payload)
        for elem in root.iter():
            local_name = elem.tag.rsplit("}", 1)[-1]
            if local_name not in {"Envelope", "Header", "Body"}:
                return local_name
    except ET.ParseError:
        pass

    match = re.search(r"<(?:[A-Za-z0-9_]+:)?([A-Za-z][A-Za-z0-9_]*)\b", payload)
    return match.group(1) if match else "unknown"


def ppm_response(payload):
    method = ppm_method(payload)
    if method == "getInitialEndpointConfiguration":
        response = initial_endpoint_response()
    elif method == "getAllEndpointConfiguration":
        response = all_endpoint_response()
    elif method == "getDeviceData":
        response = device_data_response()
    elif method == "getHomeServer":
        response = home_server_response()
    elif method == "getHomeCapabilities":
        response = home_capabilities_response()
    elif method == "setDeviceData":
        response = ppm_success_response(method)
    elif method == "getCallHistory":
        response = call_history_response()
    elif method in {
        "deleteCallHistory",
        "getPermissionsType",
        "setVolumeSettings",
        "setOneTouchDialList",
    }:
        response = ppm_success_response(method)
    else:
        if any(word in method.lower() for word in ("contact", "device", "search", "list")):
            response = empty_list_response(method)
        else:
            response = ok_response(method)
    log(f"PPM {method} -> 200")
    return response.encode("utf-8")


ADMIN_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Avaya Gateway</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #657384;
      --line: #d8dee8;
      --accent: #5b4bd8;
      --ok: #0f8b62;
      --warn: #b45309;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      background: #111827;
      color: #fff;
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { font-size: 20px; margin: 0; font-weight: 700; }
    main { padding: 20px; max-width: 1320px; margin: 0 auto; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }
    h2 { font-size: 15px; margin: 0 0 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 64px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 5px; }
    .value { font-size: 16px; font-weight: 700; overflow-wrap: anywhere; }
    .two { display: grid; grid-template-columns: minmax(280px, 420px) minmax(0, 1fr); gap: 16px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); text-align: left; padding: 8px; vertical-align: top; }
    th { color: var(--muted); font-weight: 700; }
    .interfaces { display: grid; gap: 8px; }
    .iface {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      display: grid;
      grid-template-columns: 24px 1fr;
      gap: 8px;
      align-items: start;
    }
    .iface strong { display: block; font-size: 14px; margin-bottom: 3px; }
    .iface span { display: block; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    button, select {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 0 12px;
      font-size: 13px;
    }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .toolbar { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
    pre {
      background: #0b1020;
      color: #dbeafe;
      border-radius: 6px;
      padding: 12px;
      margin: 0;
      min-height: 360px;
      max-height: 560px;
      overflow: auto;
      font-size: 12px;
      line-height: 1.35;
      white-space: pre-wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 0 9px;
      background: #eef2ff;
      color: #312e81;
      font-size: 12px;
      font-weight: 700;
    }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .two { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 12px; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Avaya Gateway</h1>
    <span id="statusPill" class="pill">cargando</span>
  </header>
  <main>
    <section>
      <h2>Estado</h2>
      <div class="grid">
        <div class="metric"><div class="label">IP publicada</div><div class="value" id="advertisedIp">-</div></div>
        <div class="metric"><div class="label">Interfaz</div><div class="value" id="selectedInterface">-</div></div>
        <div class="metric"><div class="label">SIP remoto</div><div class="value" id="sipRemote">-</div></div>
        <div class="metric"><div class="label">Uptime</div><div class="value" id="uptime">-</div></div>
      </div>
    </section>

    <div class="two">
      <section>
        <h2>Red</h2>
        <div id="interfaces" class="interfaces"></div>
        <div class="toolbar">
          <button id="saveInterface" class="primary">Aplicar</button>
          <button id="refreshInterfaces">Actualizar</button>
        </div>
      </section>

      <section>
        <h2>Telefonos</h2>
        <table>
          <thead>
            <tr>
              <th>IP</th>
              <th>Usuario</th>
              <th>Estado</th>
              <th>Registro</th>
              <th>Ultimo</th>
            </tr>
          </thead>
          <tbody id="phones"></tbody>
        </table>
      </section>
    </div>

    <section>
      <h2>Logs</h2>
      <div class="toolbar" style="margin-top:0; margin-bottom:10px;">
        <select id="logFile"></select>
        <button id="refreshLogs">Actualizar</button>
      </div>
      <pre id="logOutput"></pre>
    </section>
  </main>

  <script>
    let selectedInterface = "auto";

    function fmtAge(seconds) {
      seconds = Math.max(0, Math.floor(seconds || 0));
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      if (h) return `${h}h ${m}m`;
      if (m) return `${m}m ${s}s`;
      return `${s}s`;
    }

    function esc(value) {
      return String(value ?? "-").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch]));
    }

    async function api(path, options) {
      const res = await fetch(path, options || {});
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    function renderState(state) {
      document.getElementById("statusPill").textContent = "online";
      document.getElementById("advertisedIp").textContent = state.advertised_ip || "-";
      document.getElementById("selectedInterface").textContent = state.selected_interface || "auto";
      document.getElementById("sipRemote").textContent = state.sip_remote || "-";
      document.getElementById("uptime").textContent = fmtAge(state.uptime_seconds);
      renderPhones(state.phones || []);
      renderLogSelect(state.logs || []);
    }

    function renderPhones(phones) {
      const body = document.getElementById("phones");
      body.innerHTML = "";
      if (!phones.length) {
        body.innerHTML = '<tr><td colspan="5">Sin telefonos vistos todavia.</td></tr>';
        return;
      }
      for (const phone of phones) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${esc(phone.ip)}:${esc(phone.port)}</td>
          <td>${esc(phone.user)}</td>
          <td>${esc(phone.status)}</td>
          <td>${esc(phone.registration_code)}</td>
          <td>${esc(phone.last_seen)}</td>`;
        body.appendChild(tr);
      }
    }

    function renderLogSelect(logs) {
      const select = document.getElementById("logFile");
      const current = select.value;
      select.innerHTML = "";
      for (const log of logs) {
        const option = document.createElement("option");
        option.value = log.name;
        option.textContent = log.name;
        select.appendChild(option);
      }
      if (logs.some(log => log.name === current)) select.value = current;
    }

    function renderInterfaces(data) {
      const root = document.getElementById("interfaces");
      root.innerHTML = "";
      const auto = document.createElement("label");
      auto.className = "iface";
      auto.innerHTML = `
        <input type="radio" name="iface" value="auto">
        <div><strong>Automatico</strong><span>IP actual: ${esc(data.auto_ip)}</span></div>`;
      root.appendChild(auto);

      for (const item of data.interfaces || []) {
        const value = item.alias || item.ip;
        const label = document.createElement("label");
        label.className = "iface";
        label.innerHTML = `
          <input type="radio" name="iface" value="${esc(value)}">
          <div>
            <strong>${esc(item.alias || item.name || item.ip)}</strong>
            <span>${esc(item.ip)} ${item.status ? " / " + esc(item.status) : ""}</span>
            <span>${esc(item.description || "")}</span>
          </div>`;
        root.appendChild(label);
      }

      selectedInterface = data.selected_interface || "auto";
      const radios = Array.from(root.querySelectorAll('input[name="iface"]'));
      const match = radios.find(radio => radio.value === selectedInterface) || root.querySelector('input[value="auto"]');
      if (match) match.checked = true;
    }

    async function refreshState() {
      try {
        renderState(await api("/admin/api/state"));
      } catch (err) {
        document.getElementById("statusPill").textContent = "error";
      }
    }

    async function refreshInterfaces() {
      renderInterfaces(await api("/admin/api/interfaces"));
    }

    async function saveInterface() {
      const selected = document.querySelector('input[name="iface"]:checked');
      const value = selected ? selected.value : "auto";
      await api("/admin/api/interface", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({interface: value})
      });
      await refreshInterfaces();
      await refreshState();
      await refreshLogs();
    }

    async function refreshLogs() {
      const name = document.getElementById("logFile").value;
      if (!name) return;
      const data = await api(`/admin/api/logs?file=${encodeURIComponent(name)}&lines=260`);
      document.getElementById("logOutput").textContent = data.content || "";
    }

    document.getElementById("saveInterface").addEventListener("click", saveInterface);
    document.getElementById("refreshInterfaces").addEventListener("click", refreshInterfaces);
    document.getElementById("refreshLogs").addEventListener("click", refreshLogs);
    document.getElementById("logFile").addEventListener("change", refreshLogs);

    refreshState().then(refreshLogs);
    refreshInterfaces();
    setInterval(refreshState, 4000);
    setInterval(refreshLogs, 8000);
  </script>
</body>
</html>
"""


def json_bytes(data):
    return json.dumps(data, ensure_ascii=True).encode("utf-8")


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def default_route_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((SIP_REMOTE[0], SIP_REMOTE[1]))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except OSError:
        pass
    return SIP_ADVERTISE_HOST


def powershell_interfaces():
    if platform.system().lower() != "windows":
        return []
    script = r"""
$items = Get-NetIPConfiguration |
  Where-Object { $_.IPv4Address -and $_.NetAdapter.Status -ne 'Disabled' } |
  ForEach-Object {
    [PSCustomObject]@{
      alias = $_.InterfaceAlias
      index = $_.InterfaceIndex
      description = $_.InterfaceDescription
      ip = @($_.IPv4Address)[0].IPAddress
      status = $_.NetAdapter.Status
    }
  }
$items | ConvertTo-Json -Compress
"""
    try:
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        ).strip()
        if not output:
            return []
        parsed = json.loads(output)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return [
            {
                "alias": str(item.get("alias") or ""),
                "index": str(item.get("index") or ""),
                "description": str(item.get("description") or ""),
                "ip": str(item.get("ip") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in parsed
            if item.get("ip")
        ]
    except Exception:
        return []


def fallback_interfaces():
    seen = set()
    items = []
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        host_ips = []
    for ip in [default_route_ip()] + host_ips:
        if not ip or ip in seen or ip.startswith("127.") or ip.startswith("169.254."):
            continue
        seen.add(ip)
        items.append(
            {
                "alias": ip,
                "index": "",
                "description": "Detected local IPv4",
                "ip": ip,
                "status": "Up",
            }
        )
    return items


def network_interfaces():
    items = powershell_interfaces() or fallback_interfaces()
    return sorted(items, key=lambda item: (item.get("alias") or item.get("ip") or "").lower())


def selected_interface_name():
    return os.environ.get("AVAYA_INTERFACE_ALIAS", "").strip() or "auto"


def ip_for_interface(alias):
    if not alias or alias.lower() in {"auto", "detect", "dhcp"}:
        return default_route_ip()
    for item in network_interfaces():
        if alias in {item.get("alias"), item.get("index"), item.get("ip")}:
            return item.get("ip") or default_route_ip()
    return default_route_ip()


def read_env_values():
    path = Path(ENV_PATH)
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_values(updates):
    path = Path(ENV_PATH)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    seen = set()
    output = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n", encoding="ascii")


def replace_or_add_settings(lines, pattern, new_line):
    regex = re.compile(pattern, re.IGNORECASE)
    found = False
    changed = False
    output = []
    for line in lines:
        if regex.match(line):
            found = True
            changed = changed or line != new_line
            output.append(new_line)
        else:
            output.append(line)
    if not found:
        output.append(new_line)
        changed = True
    return output, changed


def update_phone_settings(server_ip):
    settings_path = Path(HTTP_ROOT) / "46xxsettings.txt"
    if not settings_path.exists():
        return False
    lines = settings_path.read_text(encoding="utf-8", errors="replace").splitlines()
    replacements = (
        (r"^SET\s+SIP_CONTROLLER_LIST\s+", f"SET SIP_CONTROLLER_LIST {server_ip}:5060;transport=udp"),
        (r"^SET\s+CONFIGURATION_SERVER\s+", f"SET CONFIGURATION_SERVER {server_ip}"),
        (r"^SET\s+LOGOS\s+", f"SET LOGOS {LOGO_LABEL}=http://{server_ip}/ltm-logo-232x140.jpg"),
        (r"^SET\s+LOGSRVR\s+", f"SET LOGSRVR {server_ip}"),
    )
    changed_any = False
    for pattern, new_line in replacements:
        lines, changed = replace_or_add_settings(lines, pattern, new_line)
        changed_any = changed_any or changed
    if changed_any:
        settings_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return changed_any


def set_runtime_advertise_host(server_ip):
    global SIP_ADVERTISE_HOST, SIP_ADVERTISE_HOSTPORT, LOGO_URL
    SIP_ADVERTISE_HOST = server_ip
    SIP_ADVERTISE_HOSTPORT = f"{SIP_ADVERTISE_HOST}:{SIP_ADVERTISE_PORT}"
    LOGO_URL = f"http://{SIP_ADVERTISE_HOST}/ltm-logo-232x140.jpg"
    os.environ["SIP_ADVERTISE_HOST"] = SIP_ADVERTISE_HOST
    os.environ["AVAYA_LOGO_URL"] = LOGO_URL


def apply_interface_selection(interface_value):
    interface_value = (interface_value or "auto").strip()
    if interface_value.lower() in {"auto", "detect", "dhcp"}:
        alias = ""
        env_alias = ""
        server_ip = default_route_ip()
    else:
        alias = interface_value
        env_alias = interface_value
        server_ip = ip_for_interface(interface_value)

    set_runtime_advertise_host(server_ip)
    update_phone_settings(server_ip)
    os.environ["AVAYA_INTERFACE_ALIAS"] = env_alias
    write_env_values(
        {
            "SIP_ADVERTISE_HOST": "auto",
            "AVAYA_INTERFACE_ALIAS": env_alias,
            "AVAYA_LOGO_URL": "auto",
        }
    )
    log(f"ADMIN interface set to {alias or 'auto'} ({server_ip})")
    return {"interface": alias or "auto", "ip": server_ip}


def log_files():
    root = Path(LOG_DIR)
    if not root.exists():
        return []
    files = []
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix.lower() in {".log", ".err", ".out"}:
            files.append({"name": path.name, "size": path.stat().st_size})
    return files


def tail_file(path, lines=200):
    max_lines = max(10, min(int(lines or 200), 2000))
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = 4096
        data = b""
        while size > 0 and data.count(b"\n") <= max_lines:
            read_size = min(block, size)
            size -= read_size
            handle.seek(size)
            data = handle.read(read_size) + data
    text = data.decode("utf-8", errors="replace").splitlines()
    return "\n".join(text[-max_lines:])


def admin_state():
    with state_lock:
        phones = [dict(item) for item in phone_registry.values()]
        events = list(runtime_events[-80:])
    phones.sort(key=lambda item: item.get("last_seen_epoch", 0), reverse=True)
    for phone in phones:
        phone.pop("last_seen_epoch", None)
    return {
        "app": "AvayaGateway",
        "now": now_text(),
        "uptime_seconds": int(time.time() - START_TIME),
        "advertised_ip": SIP_ADVERTISE_HOST,
        "selected_interface": selected_interface_name(),
        "sip_remote": f"{SIP_REMOTE[0]}:{SIP_REMOTE[1]}",
        "http_port": HTTP_PORT,
        "sip_port": SIP_LISTEN[1],
        "syslog_port": os.environ.get("SYSLOG_PORT", "514"),
        "phones": phones,
        "logs": log_files(),
        "events": events,
    }


def admin_interfaces():
    return {
        "auto_ip": default_route_ip(),
        "selected_interface": selected_interface_name(),
        "advertised_ip": SIP_ADVERTISE_HOST,
        "interfaces": network_interfaces(),
    }


class AvayaHTTPHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "AvayaShim/1.0"

    def log_message(self, fmt, *args):
        log(f"HTTP {self.client_address[0]} {fmt % args}")

    def send_body(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200):
        self.send_body(status, "application/json; charset=utf-8", json_bytes(data))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/admin", "/admin/"}:
            self.send_body(200, "text/html; charset=utf-8", ADMIN_HTML.encode("utf-8"))
            return
        if parsed.path == "/admin/api/state":
            self.send_json(admin_state())
            return
        if parsed.path == "/admin/api/interfaces":
            self.send_json(admin_interfaces())
            return
        if parsed.path == "/admin/api/logs":
            query = urllib.parse.parse_qs(parsed.query)
            name = os.path.basename((query.get("file") or [""])[0])
            lines = (query.get("lines") or ["200"])[0]
            allowed = {item["name"] for item in log_files()}
            if not name or name not in allowed:
                self.send_json({"error": "unknown log file", "content": ""}, status=404)
                return
            try:
                content = tail_file(Path(LOG_DIR) / name, lines)
            except OSError as exc:
                self.send_json({"error": str(exc), "content": ""}, status=500)
                return
            self.send_json({"name": name, "content": content})
            return
        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8", errors="replace")
        if self.path.rstrip("/") == "/axis/services/PPM":
            data = ppm_response(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.rstrip("/") == "/admin/api/interface":
            try:
                data = json.loads(payload or "{}")
                interface_value = data.get("interface", "auto")
                result = apply_interface_selection(interface_value)
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"ok": True, **result, "state": admin_state()})
            return
        self.send_error(404, "Unknown POST target")


def get_register_expires(text):
    match = re.search(r"^Contact:.*?expires=(\d+)", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1) if match else SIP_EXPIRES_DEFAULT


def add_expires_header(text, value):
    if re.search(r"^Expires\s*:", text, re.IGNORECASE | re.MULTILINE):
        return text
    header = f"Expires: {value}\r\n"
    if re.search(r"^Content-Length\s*:", text, re.IGNORECASE | re.MULTILINE):
        return re.sub(r"(?im)^Content-Length\s*:", header + "Content-Length:", text, count=1)
    return text + "\r\n" + header


def set_expires_header(text, value):
    if re.search(r"^Expires\s*:", text, re.IGNORECASE | re.MULTILINE):
        return re.sub(r"(?im)^Expires\s*:\s*\d+\s*$", f"Expires: {value}", text, count=1)
    return add_expires_header(text, value)


def sip_request_method(text):
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.upper().startswith("SIP/2.0"):
        return None
    parts = first_line.split()
    return parts[0].upper() if parts else None


def sip_request_uri(text):
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.upper().startswith("SIP/2.0"):
        return None
    parts = first_line.split()
    if len(parts) >= 3 and parts[2].upper().startswith("SIP/2.0"):
        return parts[1]
    return None


def sip_cseq_method(text):
    match = re.search(r"^CSeq:\s*\d+\s+([A-Z]+)\b", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).upper() if match else None


def sip_response_status(text):
    match = re.search(r"^SIP/2\.0\s+(\d+)\s*(.*)$", text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def sip_header_values(text, header_name):
    values = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.lower().startswith(header_name.lower() + ":"):
            values.append(line.split(":", 1)[1].strip())
    return values


def sip_header_value(text, header_name):
    values = sip_header_values(text, header_name)
    return values[0] if values else None


def sip_call_id(text):
    return sip_header_value(text, "Call-ID")


def first_sip_uri(value):
    if not value:
        return None
    match = re.search(r"<(sip:[^>]+)>", value, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b(sip:[^\s,;>]+(?:;[^\s,>]*)?)", value, re.IGNORECASE)
    return match.group(1) if match else None


def uri_hostport(uri):
    if not uri:
        return None
    match = re.search(r"(?i)sip:(?:[^@;>\s]+@)?([^;>\s]+)", uri)
    return match.group(1) if match else None


def rewrite_request_uri(text, method, uri):
    pattern = rf"(?im)^({method}\s+)sip:[^\s]+(\s+SIP/2\.0)"
    return re.sub(pattern, lambda match: match.group(1) + uri + match.group(2), text, count=1)


def rewrite_header_uri(text, header_name, uri):
    pattern = rf"(?im)^({re.escape(header_name)}:\s*(?:.*?<)?)(sip:[^>\s,]+)"
    return re.sub(pattern, lambda match: match.group(1) + uri, text, count=1)


def rewrite_header_uri_host(text, header_name, hostport):
    pattern = rf"(?im)^({re.escape(header_name)}:\s*(?:.*?<)?sip:(?:[^@;>\s]+@)?)([^;>\s]+)"
    return re.sub(pattern, lambda match: match.group(1) + hostport, text)


def strip_via_received_from_shim(text):
    pattern = rf"(?im)(^Via:.*?);received={re.escape(SIP_ADVERTISE_HOST)}(?=;|,|\s|$)"
    return re.sub(pattern, r"\1", text)


def patch_dialog_response_to_shim(text):
    if sip_cseq_method(text) != "INVITE":
        return text
    status = sip_response_status(text)
    if not status or status[0] not in {"183", "200"}:
        return text

    call_id = sip_call_id(text)
    if not call_id:
        return text

    contact_uri = first_sip_uri(sip_header_value(text, "Contact"))
    record_route_uri = first_sip_uri(sip_header_value(text, "Record-Route"))
    state = dialog_routes.setdefault(call_id, {})
    if contact_uri:
        state["remote_contact_uri"] = contact_uri
    if record_route_uri:
        state["remote_route_hostport"] = uri_hostport(record_route_uri)

    patched = rewrite_header_uri_host(text, "Record-Route", SIP_ADVERTISE_HOSTPORT)
    patched = rewrite_header_uri_host(patched, "Contact", SIP_ADVERTISE_HOSTPORT)
    log(f"SIP zadarma->phone response {status[0]} for INVITE dialog routed via shim")
    return patched


def patch_incoming_invite_to_phone(text):
    if sip_request_method(text) != "INVITE":
        return text

    call_id = sip_call_id(text)
    if call_id:
        request_uri = sip_request_uri(text)
        contact_uri = first_sip_uri(sip_header_value(text, "Contact"))
        record_route_uri = first_sip_uri(sip_header_value(text, "Record-Route"))
        state = dialog_routes.setdefault(call_id, {})
        if request_uri:
            state["phone_public_uri"] = request_uri
        if contact_uri:
            state["remote_contact_uri"] = contact_uri
        if record_route_uri:
            state["remote_route_hostport"] = uri_hostport(record_route_uri)

    patched = re.sub(
        r"(?im)^INVITE\s+sip:[^\s]+\s+SIP/2\.0",
        f"INVITE sip:{EXTENSION}@{DOMAIN} SIP/2.0",
        text,
        count=1,
    )
    patched = rewrite_header_uri_host(patched, "Record-Route", SIP_ADVERTISE_HOSTPORT)
    patched = rewrite_header_uri_host(patched, "Contact", SIP_ADVERTISE_HOSTPORT)
    log("SIP zadarma->phone INVITE routed via shim")
    return patched


def patch_dialog_request_to_phone(text):
    method = sip_request_method(text)
    if method not in {"ACK", "BYE", "CANCEL", "INVITE", "UPDATE", "INFO", "REFER"}:
        return text

    call_id = sip_call_id(text)
    if not dialog_routes.get(call_id):
        return text

    patched = rewrite_request_uri(text, method, f"sip:{EXTENSION}@{DOMAIN}")
    patched = rewrite_header_uri_host(patched, "Route", SIP_ADVERTISE_HOSTPORT)
    patched = rewrite_header_uri_host(patched, "Record-Route", SIP_ADVERTISE_HOSTPORT)
    patched = rewrite_header_uri_host(patched, "Contact", SIP_ADVERTISE_HOSTPORT)
    if patched != text:
        log(f"SIP zadarma->phone {method} dialog routed via shim")
    return patched


def restore_dialog_response_to_remote(text):
    status = sip_response_status(text)
    if not status:
        return text

    cseq_method = sip_cseq_method(text)
    if cseq_method not in {"INVITE", "BYE", "CANCEL", "UPDATE", "INFO", "REFER"}:
        return text

    call_id = sip_call_id(text)
    state = dialog_routes.get(call_id)
    if not state:
        return text

    patched = strip_via_received_from_shim(text)
    remote_route_hostport = state.get("remote_route_hostport")
    phone_public_uri = state.get("phone_public_uri")

    if remote_route_hostport:
        patched = rewrite_header_uri_host(patched, "Record-Route", remote_route_hostport)

    if cseq_method == "INVITE" and phone_public_uri and sip_header_value(patched, "Contact"):
        patched = rewrite_header_uri(patched, "Contact", phone_public_uri)

    if patched != text:
        code, reason = status
        suffix = f" {reason}" if reason else ""
        log(f"SIP phone->zadarma response {code}{suffix} for {cseq_method} dialog restored toward Zadarma")
    return patched


def restore_dialog_request_to_remote(text):
    method = sip_request_method(text)
    if method not in {"ACK", "BYE", "CANCEL", "INVITE", "UPDATE", "INFO", "REFER"}:
        return text

    call_id = sip_call_id(text)
    state = dialog_routes.get(call_id)
    if not state:
        return text

    remote_contact_uri = state.get("remote_contact_uri")
    remote_route_hostport = state.get("remote_route_hostport")
    patched = text

    if remote_contact_uri:
        pattern = rf"(?im)^({method}\s+)sip:[^\s]*{re.escape(SIP_ADVERTISE_HOST)}[^\s]*(\s+SIP/2\.0)"
        patched = re.sub(pattern, lambda match: match.group(1) + remote_contact_uri + match.group(2), patched, count=1)

    if remote_route_hostport:
        patched = re.sub(
            rf"(?im)^(Route:\s*(?:.*?<)?sip:)(?:{re.escape(SIP_ADVERTISE_HOSTPORT)}|{re.escape(SIP_ADVERTISE_HOST)})(?=[;>\s])",
            lambda match: match.group(1) + remote_route_hostport,
            patched,
        )

    if patched != text:
        log(f"SIP phone->zadarma {method} dialog route restored toward Zadarma")
    return patched


def to_header_with_tag(value):
    if re.search(r";\s*tag=", value, re.IGNORECASE):
        return value
    return f"{value};tag=avaya-shim"


def sip_response_bytes(request_text, code, reason, extra_headers=None, body=b""):
    via_values = sip_header_values(request_text, "Via")
    from_value = sip_header_value(request_text, "From")
    to_value = sip_header_value(request_text, "To")
    call_id = sip_header_value(request_text, "Call-ID")
    cseq = sip_header_value(request_text, "CSeq")
    if not via_values or not from_value or not to_value or not call_id or not cseq:
        return None

    lines = [f"SIP/2.0 {code} {reason}"]
    lines.extend(f"Via: {value}" for value in via_values)
    lines.extend(
        [
            f"From: {from_value}",
            f"To: {to_header_with_tag(to_value)}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq}",
            f"Contact: <sip:{SIP_ADVERTISE_HOSTPORT};transport=udp>",
            "Server: AvayaShim",
        ]
    )
    if extra_headers:
        lines.extend(extra_headers)
    lines.extend([f"Content-Length: {len(body)}", "", ""])
    return "\r\n".join(lines).encode("utf-8") + body


def build_options_ok(request_text):
    via_values = sip_header_values(request_text, "Via")
    from_value = sip_header_value(request_text, "From")
    to_value = sip_header_value(request_text, "To")
    call_id = sip_header_value(request_text, "Call-ID")
    cseq = sip_header_value(request_text, "CSeq")
    if not via_values or not from_value or not to_value or not call_id or not cseq:
        return None

    lines = ["SIP/2.0 200 OK"]
    lines.extend(f"Via: {value}" for value in via_values)
    lines.extend(
        [
            f"From: {from_value}",
            f"To: {to_header_with_tag(to_value)}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq}",
            "Server: AvayaShim",
            "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REGISTER",
            "Accept: application/sdp",
            "Content-Length: 0",
            "",
            "",
        ]
    )
    return "\r\n".join(lines).encode("utf-8")


def subscription_expires(request_text):
    value = sip_header_value(request_text, "Expires")
    try:
        requested = int(value) if value else 3600
    except ValueError:
        requested = 3600
    return max(60, min(requested, 3600))


def build_dialog_info_body(version=0):
    return f"""<?xml version="1.0"?>
<dialog-info xmlns="urn:ietf:params:xml:ns:dialog-info" version="{version}" state="full" entity="sip:{EXTENSION}@{DOMAIN}">
</dialog-info>"""


def build_reginfo_body():
    return f"""<?xml version="1.0"?>
<reginfo xmlns="urn:ietf:params:xml:ns:reginfo" version="0" state="full">
  <registration aor="sip:{EXTENSION}@{DOMAIN}" id="avaya-shim-reg" state="active">
    <contact id="avaya-shim-contact" state="active" event="registered" expires="3600">
      <uri>sip:{EXTENSION}@{SIP_ADVERTISE_HOSTPORT};transport=udp</uri>
    </contact>
  </registration>
</reginfo>"""


def build_message_summary_body():
    return (
        "Messages-Waiting: no\r\n"
        f"Message-Account: sip:{EXTENSION}@{DOMAIN}\r\n"
        "Voice-Message: 0/0 (0/0)\r\n"
    )


def build_avaya_feature_status_body():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feature-status entity="sip:{EXTENSION}@{DOMAIN}" version="0" state="full">
  <feature name="AST" status="active" />
  <feature name="PPM" status="active" />
  <feature name="DeviceData" status="active" />
</feature-status>"""


def build_subscribe_ok_and_notify(request_text, content_type, body_text):
    event = sip_header_value(request_text, "Event") or ""
    via_values = sip_header_values(request_text, "Via")
    from_value = sip_header_value(request_text, "From")
    to_value = sip_header_value(request_text, "To")
    call_id = sip_header_value(request_text, "Call-ID")
    cseq = sip_header_value(request_text, "CSeq")
    if not via_values or not from_value or not to_value or not call_id or not cseq:
        return None

    server_to = to_header_with_tag(to_value)
    contact_uri = first_sip_uri(sip_header_value(request_text, "Contact")) or first_sip_uri(from_value)
    if not contact_uri:
        return None

    expires = subscription_expires(request_text)
    contact = f"<sip:{SIP_ADVERTISE_HOSTPORT};transport=udp>"
    ok_lines = ["SIP/2.0 200 OK"]
    ok_lines.extend(f"Via: {value}" for value in via_values)
    ok_lines.extend(
        [
            f"From: {from_value}",
            f"To: {server_to}",
            f"Call-ID: {call_id}",
            f"CSeq: {cseq}",
            f"Contact: {contact}",
            "Server: AvayaShim",
            f"Expires: {expires}",
            "Content-Length: 0",
            "",
            "",
        ]
    )

    body = body_text.encode("utf-8")
    branch = f"z9hG4bK-avaya-shim-{int(time.time() * 1000)}"
    notify_lines = [
        f"NOTIFY {contact_uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {SIP_ADVERTISE_HOSTPORT};branch={branch}",
        "Max-Forwards: 70",
        f"From: {server_to}",
        f"To: {from_value}",
        f"Call-ID: {call_id}",
        "CSeq: 1 NOTIFY",
        f"Contact: {contact}",
        "User-Agent: AvayaShim",
        f"Event: {event}",
        f"Subscription-State: active;expires={expires}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "",
        "",
    ]
    notify = "\r\n".join(notify_lines).encode("utf-8") + body
    ok = "\r\n".join(ok_lines).encode("utf-8")
    return ok, notify


def build_local_subscribe_responses(request_text):
    event = (sip_header_value(request_text, "Event") or "").split(";", 1)[0].strip().lower()
    if sip_request_method(request_text) != "SUBSCRIBE" or not event:
        return None

    if event == "avaya-cm-feature-status":
        return build_subscribe_ok_and_notify(
            request_text,
            "application/avaya-cm-feature-status+xml",
            build_avaya_feature_status_body(),
        )
    if event == "dialog":
        return build_subscribe_ok_and_notify(
            request_text,
            "application/dialog-info+xml",
            build_dialog_info_body(),
        )
    if event == "reg":
        return build_subscribe_ok_and_notify(
            request_text,
            "application/reginfo+xml",
            build_reginfo_body(),
        )
    if event == "message-summary":
        return build_subscribe_ok_and_notify(
            request_text,
            "application/simple-message-summary",
            build_message_summary_body(),
        )
    if event == "avaya-ccs-profile":
        return build_subscribe_ok_and_notify(
            request_text,
            "application/avaya-ccs-profile+xml",
            f"""<?xml version="1.0"?>
<profile entity="sip:{EXTENSION}@{DOMAIN}" version="0" state="full" />""",
        )
    return None


def build_avaya_fnu_responses(request_text):
    method = sip_request_method(request_text)
    call_id = sip_call_id(request_text)
    request_uri = sip_request_uri(request_text) or ""
    to_value = sip_header_value(request_text, "To") or ""
    is_fnu = "avaya-cm-fnu=" in request_uri.lower() or "avaya-cm-fnu=" in to_value.lower()

    if method == "INVITE" and is_fnu and call_id:
        local_fnu_invites[call_id] = request_text
        code = AVAYA_FNU_INVITE_RESPONSE.strip()
        reason = "Session Progress" if code == "183" else "OK"
        response = sip_response_bytes(
            request_text,
            code,
            reason,
            extra_headers=[
                "Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH, MESSAGE",
            ],
        )
        return [response] if response else None

    if method == "ACK" and is_fnu and call_id in local_fnu_invites:
        return []

    if method == "CANCEL" and is_fnu and call_id in local_fnu_invites:
        original_invite = local_fnu_invites.pop(call_id)
        responses = []
        cancel_ok = sip_response_bytes(request_text, "200", "OK")
        invite_terminated = sip_response_bytes(original_invite, "487", "Request Terminated")
        if cancel_ok:
            responses.append(cancel_ok)
        if invite_terminated:
            responses.append(invite_terminated)
        return responses

    if method == "BYE" and is_fnu and call_id in local_fnu_invites:
        local_fnu_invites.pop(call_id, None)
        response = sip_response_bytes(request_text, "200", "OK")
        return [response] if response else None

    return None


def build_zadarma_notify_ok(request_text):
    if sip_request_method(request_text) != "NOTIFY":
        return None
    return sip_response_bytes(request_text, "200", "OK")


def patch_invite_503_for_phone(text):
    status = sip_response_status(text)
    if not status or status[0] != "503" or sip_cseq_method(text) != "INVITE":
        return text
    patched = re.sub(
        r"(?im)^SIP/2\.0\s+503\b.*$",
        "SIP/2.0 480 Temporarily Unavailable",
        text,
        count=1,
    )
    if patched != text:
        log("SIP zadarma->phone INVITE response 503 rewritten to 480 to keep Avaya registered")
    return patched


def log_sip_summary(text, direction):
    request_method = sip_request_method(text)
    cseq_method = sip_cseq_method(text)
    status = sip_response_status(text)

    if request_method in {"INVITE", "ACK", "CANCEL", "BYE"}:
        uri = sip_request_uri(text)
        suffix = f" {uri}" if request_method == "INVITE" and uri else ""
        log(f"SIP {direction} {request_method}{suffix}")
    elif request_method == "SUBSCRIBE":
        event = sip_header_value(text, "Event") or ""
        log(f"SIP {direction} SUBSCRIBE {event}")
    elif status and cseq_method in {"INVITE", "ACK", "CANCEL", "BYE"}:
        code, reason = status
        suffix = f" {reason}" if reason else ""
        log(f"SIP {direction} response {code}{suffix} for {cseq_method}")


def patch_sip_text(text, direction):
    if direction == "zadarma->phone":
        if sip_request_method(text) == "INVITE":
            text = patch_incoming_invite_to_phone(text)
        elif sip_request_method(text):
            text = patch_dialog_request_to_phone(text)
        else:
            text = patch_dialog_response_to_shim(text)
            text = patch_invite_503_for_phone(text)
    elif direction == "phone->zadarma":
        text = restore_dialog_response_to_remote(text)
        text = restore_dialog_request_to_remote(text)
        if sip_request_method(text) == "INVITE":
            before = text
            text = set_expires_header(text, SIP_INVITE_EXPIRES_DEFAULT)
            if text != before:
                log(f"SIP phone->zadarma INVITE Expires set to {SIP_INVITE_EXPIRES_DEFAULT}")

    is_register = re.search(r"^CSeq:\s*\d+\s+REGISTER\b", text, re.IGNORECASE | re.MULTILINE)
    if is_register:
        text = text.replace(";avaya-sc-enabled", "")
        expires = get_register_expires(text)
        text = add_expires_header(text, expires)
        log(f"SIP {direction} REGISTER patched Expires={expires}")
    else:
        log_sip_summary(text, direction)
    return text


def patch_sip_packet(data, direction):
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
        encoding = "latin-1"
    patched = patch_sip_text(text, direction)
    return patched.encode(encoding)


def sip_uri_user(uri):
    if not uri:
        return ""
    match = re.search(r"(?i)sip:([^@;>\s]+)", uri)
    return match.group(1) if match else ""


def phone_registry_key(addr):
    return f"{addr[0]}:{addr[1]}"


def update_phone_record(addr, **updates):
    key = phone_registry_key(addr)
    with state_lock:
        record = phone_registry.setdefault(
            key,
            {
                "ip": addr[0],
                "port": addr[1],
                "user": "",
                "status": "seen",
                "registration_code": "",
                "expires": "",
                "user_agent": "",
                "last_seen": "",
                "last_seen_epoch": 0,
            },
        )
        record.update(updates)
        record["ip"] = addr[0]
        record["port"] = addr[1]
        record["last_seen"] = now_text()
        record["last_seen_epoch"] = time.time()


def record_phone_packet(addr, text):
    method = sip_request_method(text)
    update_phone_record(addr, status="active")

    if method != "REGISTER":
        return

    call_id = sip_call_id(text)
    if call_id:
        register_call_routes[call_id] = addr

    from_uri = first_sip_uri(sip_header_value(text, "From"))
    contact = sip_header_value(text, "Contact") or ""
    updates = {
        "status": "registering",
        "user": sip_uri_user(from_uri) or sip_uri_user(contact) or EXTENSION,
        "expires": get_register_expires(text),
        "user_agent": sip_header_value(text, "User-Agent") or "",
        "contact": contact,
    }
    update_phone_record(addr, **updates)


def record_remote_register_response(text, fallback_addr=None):
    if sip_cseq_method(text) != "REGISTER":
        return
    status = sip_response_status(text)
    if not status:
        return

    call_id = sip_call_id(text)
    addr = register_call_routes.get(call_id) or fallback_addr
    if not addr:
        return

    code, reason = status
    if code == "200":
        state = "registered"
    elif code in {"401", "407"}:
        state = "auth challenge"
    else:
        state = "registration error"

    expires = sip_header_value(text, "Expires") or ""
    contact = sip_header_value(text, "Contact") or ""
    update_phone_record(
        addr,
        status=state,
        registration_code=f"{code} {reason}".strip(),
        expires=expires,
        contact=contact or phone_registry.get(phone_registry_key(addr), {}).get("contact", ""),
    )


def sip_proxy_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(SIP_LISTEN)
    log(f"SIP proxy listening on udp/{SIP_LISTEN[1]}, forwarding to {SIP_REMOTE[0]}:{SIP_REMOTE[1]}")
    phone_addr = None
    while True:
        data, addr = sock.recvfrom(65535)
        if addr[0] == SIP_REMOTE[0]:
            try:
                remote_text = data.decode("utf-8")
            except UnicodeDecodeError:
                remote_text = data.decode("latin-1", errors="replace")
            record_remote_register_response(remote_text, phone_addr)
            if sip_request_method(remote_text) == "OPTIONS":
                response = build_options_ok(remote_text)
                if response is not None:
                    sock.sendto(response, addr)
                    log(f"SIP zadarma->shim OPTIONS answered 200 OK to {addr[0]}:{addr[1]}")
                    continue
                log("SIP zadarma->shim OPTIONS missing required headers; forwarding")
            notify_ok = build_zadarma_notify_ok(remote_text)
            if notify_ok is not None:
                sock.sendto(notify_ok, addr)
                event = sip_header_value(remote_text, "Event") or ""
                log(f"SIP zadarma->shim NOTIFY {event} answered 200 OK and dropped")
                continue
            if phone_addr is None:
                log(f"SIP remote packet from {addr}, no phone mapping yet")
                continue
            patched = patch_sip_packet(data, "zadarma->phone")
            sock.sendto(patched, phone_addr)
        else:
            if phone_addr != addr:
                phone_addr = addr
                log(f"SIP phone mapping set to {phone_addr[0]}:{phone_addr[1]}")
            try:
                phone_text = data.decode("utf-8")
            except UnicodeDecodeError:
                phone_text = data.decode("latin-1", errors="replace")
            record_phone_packet(addr, phone_text)

            avaya_fnu_responses = build_avaya_fnu_responses(phone_text)
            if avaya_fnu_responses is not None:
                for response in avaya_fnu_responses:
                    sock.sendto(response, addr)
                method = sip_request_method(phone_text)
                request_uri = sip_request_uri(phone_text) or ""
                log(f"SIP phone->shim {method} {request_uri} answered locally for Avaya FNU")
                continue

            local_subscribe_responses = build_local_subscribe_responses(phone_text)
            if local_subscribe_responses is not None:
                ok, notify = local_subscribe_responses
                sock.sendto(ok, addr)
                sock.sendto(notify, addr)
                event = sip_header_value(phone_text, "Event") or ""
                log(f"SIP phone->shim SUBSCRIBE {event} answered locally with NOTIFY active")
                continue

            patched = patch_sip_packet(data, "phone->zadarma")
            sock.sendto(patched, SIP_REMOTE)


def http_loop():
    handler = functools.partial(AvayaHTTPHandler, directory=HTTP_ROOT)
    class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with ReusableThreadingTCPServer(("", HTTP_PORT), handler) as server:
        log(f"HTTP/PPM listening on tcp/{HTTP_PORT}, serving {HTTP_ROOT}")
        server.serve_forever()


if __name__ == "__main__":
    if ENABLE_SIP:
        threading.Thread(target=sip_proxy_loop, daemon=True).start()
    if ENABLE_HTTP:
        http_loop()
    else:
        while True:
            time.sleep(3600)
