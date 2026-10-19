"""User-authenticated Nextcloud boundary. No administrator impersonation."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import httpx

MAX_BYTES = 50 * 1024 * 1024
PROPERTIES = b"""<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:resourcetype/><d:getetag/><d:getlastmodified/><d:getcontentlength/><d:getcontenttype/><oc:fileid/><oc:permissions/></d:prop></d:propfind>"""


class CloudError(RuntimeError):
    def __init__(self, status: int, message: str = 'Nextcloud request failed') -> None:
        self.status = status
        super().__init__(message)


def path_name(value: str) -> str:
    if '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid file path')
    if any(part in {'.', '..'} for part in value.split('/')):
        raise ValueError('Relative path components are not allowed')
    return '/' + str(PurePosixPath('/' + value.lstrip('/'))).lstrip('/')


@dataclass(frozen=True)
class Source:
    file_id: str
    path: str
    etag: str
    size: int
    mime: str
    folder: bool
    modified: int = 0


class Cloud:
    def __init__(
        self, base: str, username: str, password: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base = base.rstrip('/')
        self.root = '/remote.php/dav/files/' + quote(username, safe='')
        self.http = httpx.AsyncClient(
            base_url=self.base, auth=(username, password), timeout=90, follow_redirects=False, transport=transport
        )

    async def __aenter__(self) -> Cloud:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.http.aclose()

    async def identity(self) -> str:
        response = await self.http.get('/ocs/v2.php/cloud/user?format=json', headers={'OCS-APIRequest': 'true'})
        self.check(response)
        return response.json()['ocs']['data']['id']

    def check(self, response: httpx.Response) -> None:
        if not 200 <= response.status_code < 300:
            raise CloudError(response.status_code)

    def url(self, path: str) -> str:
        return self.root + quote(path_name(path), safe='/')

    async def listing(self, path: str, depth: int = 1) -> list[Source]:
        response = await self.http.request(
            'PROPFIND',
            self.url(path),
            content=PROPERTIES,
            headers={'Depth': str(depth), 'Content-Type': 'application/xml'},
        )
        self.check(response)
        if len(response.content) > 10 * 1024 * 1024 or b'<!DOCTYPE' in response.content.upper():
            raise CloudError(502, 'Invalid file listing')
        result = []
        for node in ET.fromstring(response.content).findall('{DAV:}response'):
            props = node.find("{DAV:}propstat[{DAV:}status='HTTP/1.1 200 OK']/{DAV:}prop")
            if props is None:
                continue
            href = unquote(urlsplit(node.findtext('{DAV:}href', '')).path)
            prefix = unquote(self.root)
            if not href.startswith(prefix + '/'):
                raise CloudError(502, 'Invalid file listing')
            result.append(
                Source(
                    props.findtext('{http://owncloud.org/ns}fileid', ''),
                    path_name(href[len(prefix) :]),
                    props.findtext('{DAV:}getetag', ''),
                    int(props.findtext('{DAV:}getcontentlength', '0')),
                    props.findtext('{DAV:}getcontenttype', ''),
                    props.find('{DAV:}resourcetype/{DAV:}collection') is not None,
                    int(
                        parsedate_to_datetime(
                            props.findtext('{DAV:}getlastmodified', 'Thu, 01 Jan 1970 00:00:00 GMT')
                        ).timestamp()
                    ),
                )
            )
        return result

    async def stat(self, path: str) -> Source:
        items = await self.listing(path, 0)
        if len(items) != 1:
            raise CloudError(404, 'Source unavailable')
        return items[0]

    async def download(self, path: str) -> bytes:
        return await self.download_url(self.url(path))

    async def download_url(self, url: str) -> bytes:
        chunks = []
        size = 0
        async with self.http.stream('GET', url) as response:
            self.check(response)
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise CloudError(413, 'File exceeds 50 MiB')
                chunks.append(chunk)
        return b''.join(chunks)

    async def authorize(self, source: Source) -> bool:
        try:
            current = await self.stat(source.path)
            if current.file_id != source.file_id or current.etag != source.etag:
                return False
            async with self.http.stream('GET', self.url(source.path), headers={'Range': 'bytes=0-0'}) as response:
                self.check(response)
                async for _ in response.aiter_bytes():
                    break
            return True
        except (CloudError, httpx.HTTPError):
            return False

    async def create(self, path: str, data: bytes) -> None:
        if len(data) > MAX_BYTES:
            raise CloudError(413, 'File exceeds 50 MiB')
        response = await self.http.put(self.url(path), content=data, headers={'If-None-Match': '*'})
        self.check(response)
