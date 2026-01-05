#!/usr/bin/env python3
"""
CalDAV 联通性与数据拉取测试脚本

环境变量（或命令行参数）
  CALDAV_BASE   CalDAV 基地址（默认: https://127.0.0.1/api/calendar/caldav）
  CALDAV_USER   基本认证用户名（邮箱/账户）
  CALDAV_PASS   基本认证密码
  USER_ID       测试的用户ID（整数）
  VERIFY_SSL    是否校验证书（默认: false）

用法示例：
  CALDAV_BASE=https://your.domain/api/calendar/caldav \
  CALDAV_USER=admin CALDAV_PASS=Admin123! USER_ID=1 \
  python scripts/test_caldav.py
"""

from __future__ import annotations

import os
import sys
import datetime as dt
import textwrap
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth


def getenv_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def build_url(base: str, *parts: str) -> str:
    base = base.rstrip('/')
    join = "/".join(p.strip('/') for p in parts if p is not None)
    return f"{base}/{join}" if join else base


def http_request(method: str, url: str, auth: HTTPBasicAuth, *, headers=None, data: Optional[str] = None,
                 verify: bool = False) -> requests.Response:
    headers = {
        'User-Agent': 'caldav-tester/1.0',
        **(headers or {})
    }
    resp = requests.request(method=method, url=url, headers=headers, data=data, auth=auth,
                            timeout=30, allow_redirects=True, verify=verify)
    return resp


def short(text: str, length: int = 600) -> str:
    if text is None:
        return ''
    text = str(text)
    return text if len(text) <= length else text[:length] + '...<truncated>'


def calendar_query_xml(start: dt.datetime, end: dt.datetime) -> str:
    start_utc = start.strftime('%Y%m%dT%H%M%SZ')
    end_utc = end.strftime('%Y%m%dT%H%M%SZ')
    return textwrap.dedent(f"""
    <?xml version="1.0" encoding="utf-8" ?>
    <C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
      <D:prop>
        <D:getcontenttype/>
        <D:getetag/>
        <C:calendar-data/>
      </D:prop>
      <C:filter>
        <C:comp-filter name="VCALENDAR">
          <C:comp-filter name="VEVENT">
            <C:time-range start="{start_utc}" end="{end_utc}"/>
          </C:comp-filter>
        </C:comp-filter>
      </C:filter>
    </C:calendar-query>
    """)


def propfind_xml() -> str:
    return textwrap.dedent("""
    <?xml version="1.0" encoding="utf-8" ?>
    <D:propfind xmlns:D="DAV:">
      <D:prop>
        <D:resourcetype/>
        <D:getcontenttype/>
        <D:getetag/>
      </D:prop>
    </D:propfind>
    """)


def parse_multistatus(xml_text: str) -> Tuple[int, list]:
    try:
        ns = {
            'D': 'DAV:',
            'C': 'urn:ietf:params:xml:ns:caldav'
        }
        root = ET.fromstring(xml_text)
        responses = root.findall('.//D:response', ns)
        hrefs = []
        for r in responses:
            href_el = r.find('D:href', ns)
            if href_el is not None and href_el.text:
                hrefs.append(href_el.text)
        return len(responses), hrefs
    except Exception:
        return 0, []


def main() -> int:
    base = os.environ.get('CALDAV_BASE', 'https://127.0.0.1/api/calendar/caldav')
    user = os.environ.get('CALDAV_USER', '')
    password = os.environ.get('CALDAV_PASS', '')
    user_id = os.environ.get('USER_ID', '1')
    verify_ssl = getenv_bool('VERIFY_SSL', False)

    if not user or not password:
        print('请设置 CALDAV_USER 和 CALDAV_PASS 环境变量')
        return 2

    auth = HTTPBasicAuth(user, password)

    # 1) 探测根
    url_base = base
    print(f"[1] PROPFIND {url_base}")
    r = http_request('PROPFIND', url_base, auth, headers={'Depth': '0', 'Content-Type': 'application/xml'},
                     data=propfind_xml(), verify=verify_ssl)
    print('  status:', r.status_code)
    count, hrefs = parse_multistatus(r.text)
    print('  responses:', count)

    # 2) 用户主集合
    url_user = build_url(base, 'users', str(user_id)) + '/'
    print(f"[2] PROPFIND {url_user}")
    r = http_request('PROPFIND', url_user, auth, headers={'Depth': '1', 'Content-Type': 'application/xml'},
                     data=propfind_xml(), verify=verify_ssl)
    print('  status:', r.status_code)
    count, hrefs = parse_multistatus(r.text)
    print('  responses:', count)
    if hrefs:
        print('  sample href:', hrefs[0])

    # 3) 默认日历集合
    url_default = build_url(base, 'users', str(user_id), 'default') + '/'
    print(f"[3] PROPFIND {url_default}")
    r = http_request('PROPFIND', url_default, auth, headers={'Depth': '1', 'Content-Type': 'application/xml'},
                     data=propfind_xml(), verify=verify_ssl)
    print('  status:', r.status_code)
    count, hrefs = parse_multistatus(r.text)
    print('  resources:', count)
    if hrefs:
        print('  first resource:', hrefs[0])

    # 4) REPORT time-range（近 90 天）
    end = dt.datetime.utcnow() + dt.timedelta(days=45)
    start = dt.datetime.utcnow() - dt.timedelta(days=45)
    report_body = calendar_query_xml(start, end)
    print(f"[4] REPORT calendar-query {url_default}")
    r = http_request('REPORT', url_default, auth,
                     headers={'Depth': '1', 'Content-Type': 'application/xml'}, data=report_body,
                     verify=verify_ssl)
    print('  status:', r.status_code)
    count, hrefs = parse_multistatus(r.text)
    print('  events:', count)

    # 5) 如有资源，尝试 GET 一个 .ics
    if hrefs:
        sample_href = hrefs[0]
        # 处理绝对/相对路径
        if sample_href.startswith('/'):
            url_get = f"{base.rstrip('/')}{sample_href}"
        elif sample_href.startswith('http'):
            url_get = sample_href
        else:
            url_get = build_url(url_default, sample_href)
        print(f"[5] GET {url_get}")
        r = http_request('GET', url_get, auth, verify=verify_ssl)
        print('  status:', r.status_code)
        print('  body:', short(r.text, 400))

    print('\n完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())



