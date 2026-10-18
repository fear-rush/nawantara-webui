"""Read-only source comparison with current access checks and bounded output."""

import asyncio
import difflib

from .extraction import extract


async def versions(api, user_id: str, path: str) -> dict:
    account = api.store.connection(user_id)
    async with api.cloud(account) as cloud:
        source = await cloud.stat(path)
        if not await cloud.authorize(source):
            raise ValueError('Source access unavailable')
        items = await cloud.versions(source.file_id, source.modified)
        if not await cloud.authorize(source):
            raise ValueError('Source changed or access unavailable')
    if api.store.connection(user_id)['generation'] != account['generation']:
        raise ValueError('Account connection changed')
    return {
        'file_id': source.file_id,
        'url': api.link(source.file_id),
        'current_revision': source.etag,
        'versions': items,
    }


async def source_text(api, cloud, path: str, version: str) -> dict:
    source = await cloud.stat(path)
    if source.folder or not await cloud.authorize(source):
        raise ValueError('Source access unavailable')
    if version:
        available = await cloud.versions(source.file_id, source.modified)
        if version not in {item['version'] for item in available}:
            raise ValueError('Requested retained version is unavailable')
        data = await cloud.version_bytes(source.file_id, version)
    else:
        data = await cloud.download(source.path)
    text = await asyncio.to_thread(extract, source.path, data)
    if not await cloud.authorize(source):
        raise ValueError('Source changed or access unavailable')
    return {
        'path': source.path,
        'url': api.link(source.file_id),
        'revision': version or source.etag,
        'text': text,
        '_source': source,
    }


async def compare(api, user_id: str, left: str, right: str, left_version: str = '', right_version: str = '') -> dict:
    account = api.store.connection(user_id)
    async with api.cloud(account) as cloud:
        before = await source_text(api, cloud, left, left_version)
        after = await source_text(api, cloud, right, right_version)
        # Check both again after potentially lengthy extraction of the other source.
        for item in (before, after):
            if not await cloud.authorize(item['_source']):
                raise ValueError('Source access unavailable')
    difference = '\n'.join(
        difflib.unified_diff(
            before['text'].splitlines(),
            after['text'].splitlines(),
            fromfile=f'{before["path"]} ({before["revision"]})',
            tofile=f'{after["path"]} ({after["revision"]})',
            lineterm='',
        )
    )
    if api.store.connection(user_id)['generation'] != account['generation']:
        raise ValueError('Account connection changed')
    return {
        'left': {k: v for k, v in before.items() if k not in {'text', '_source'}},
        'right': {k: v for k, v in after.items() if k not in {'text', '_source'}},
        'diff': difference[:30000],
        'truncated': len(difference) > 30000,
        'basis': 'Extracted text only; formatting, images and formulas are not compared.',
    }
