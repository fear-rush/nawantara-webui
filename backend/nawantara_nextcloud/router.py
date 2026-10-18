"""Session-authenticated Nextcloud connection, document and job endpoints."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from open_webui.models.users import UserModel, Users
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel, Field

from . import activity
from .client import CloudError, path_name
from .runtime import attachment, service, store

router = APIRouter(prefix='/api/v1/nextcloud', tags=['nextcloud'])


async def session(request: Request, user: UserModel = Depends(get_verified_user)) -> UserModel:
    if request.method != 'GET' and not request.headers.get('authorization', '').startswith('Bearer '):
        raise HTTPException(403, 'An authenticated session header is required')
    return user


class ConnectionForm(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    app_password: str = Field(min_length=1, max_length=512)
    scopes: list[str] = Field(min_length=1, max_length=20)


class SaveForm(BaseModel):
    path: str = Field(min_length=2, max_length=1000)
    text: str | None = Field(default=None, max_length=2_000_000)
    file_id: str | None = None


class ControlForm(BaseModel):
    operation: str = Field(pattern='^(cancel|retry)$')


async def safe_call(coroutine):
    try:
        return await coroutine
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except CloudError as error:
        raise HTTPException(
            403 if error.status in {401, 403, 404} else 502,
            'Nextcloud access unavailable. Check your account and file permissions.',
        ) from None
    except httpx.HTTPError:
        raise HTTPException(503, 'Nextcloud is unavailable') from None


@router.get('/connection')
async def connection(request: Request, user: UserModel = Depends(session)) -> dict:
    api = await service(request.app)
    try:
        account = api.store.connection(user.id)
        if account['instance'] != api.base:
            raise ValueError('Nextcloud server changed')
        return {'connected': True, 'username': account['username'], 'scopes': account['scopes'], 'url': api.public}
    except ValueError:
        return {'connected': False, 'url': api.public}


@router.post('/connection')
async def connect(body: ConnectionForm, request: Request, user: UserModel = Depends(session)) -> dict:
    api = await service(request.app)
    await safe_call(api.connect(user.id, body.username, body.app_password, body.scopes))
    return {'connected': True}


@router.delete('/connection')
async def disconnect(request: Request, user: UserModel = Depends(session)) -> dict:
    store(request.app).disconnect(user.id)
    return {'connected': False}


@router.get('/files')
async def files(request: Request, path: str = '/', user: UserModel = Depends(session)) -> dict:
    api = await service(request.app)
    return {'files': await safe_call(api.browse(user.id, path))}


@router.get('/search')
async def search(request: Request, query: str, user: UserModel = Depends(session), mode: str = 'hybrid') -> dict:
    api = await service(request.app)
    return await safe_call(api.search(user.id, query, mode))


@router.get('/read')
async def read(request: Request, path: str, user: UserModel = Depends(session)) -> dict:
    api = await service(request.app)
    return await safe_call(api.read(user.id, path))


@router.post('/save')
async def save(body: SaveForm, request: Request, user: UserModel = Depends(session)) -> dict:
    if (body.text is None) == (body.file_id is None):
        raise HTTPException(400, 'Choose either text or one owned attachment')
    if body.file_id:
        await safe_call(attachment(user.id, body.file_id))
    try:
        return {
            'job_id': store(request.app).enqueue(user.id, 'save', {**body.model_dump(), 'path': path_name(body.path)})
        }
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.post('/index')
async def index(request: Request, user: UserModel = Depends(session)) -> dict:
    try:
        return {'job_id': store(request.app).enqueue(user.id, 'index', {})}
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.delete('/index/{file_id}')
async def remove_index(file_id: str, request: Request, user: UserModel = Depends(session)) -> dict:
    store(request.app).exclude(user.id, file_id)
    return {'removed': True, 'source_deleted': False}


@router.get('/jobs')
async def jobs(request: Request, user: UserModel = Depends(session)) -> dict:
    return {'jobs': store(request.app).jobs(user.id)}


@router.post('/jobs/{job_id}')
async def control(job_id: str, body: ControlForm, request: Request, user: UserModel = Depends(session)) -> dict:
    try:
        store(request.app).control(job_id, user.id, body.operation)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return {'accepted': True}


@router.get('/jobs/{job_id}')
async def job_detail(job_id: str, request: Request, user: UserModel = Depends(session)) -> dict:
    try:
        return activity.detail(store(request.app), job_id, user.id)
    except KeyError:
        raise HTTPException(404, 'Job not found') from None


@router.get('/admin/jobs')
async def admin_jobs(
    request: Request,
    user: UserModel = Depends(get_admin_user),
    page: int = Query(1, ge=1),
    status: str = Query('', pattern='^(|active|attention|queued|processing|completed|failed|cancelled|interrupted)$'),
    action: str = Query('', pattern='^(|index|save)$'),
    actor: str = Query('', max_length=255),
    query: str = Query('', max_length=64),
    since: float = Query(0, ge=0),
) -> dict:
    result = activity.listing(
        store(request.app), page=page, status=status, action=action, actor=actor, query=query, since=since
    )
    actors = []
    for user_id in result['actors']:
        account = await Users.get_user_by_id(user_id)
        actors.append({'id': user_id, 'name': account.name if account else 'Deleted account'})
    names = {item['id']: item['name'] for item in actors}
    for job in result['jobs']:
        job['user_name'] = names.get(job['user_id'], 'Deleted account')
    result['actors'] = actors
    return result


@router.get('/admin/jobs/{job_id}')
async def admin_job_detail(job_id: str, request: Request, user: UserModel = Depends(get_admin_user)) -> dict:
    try:
        result = activity.detail(store(request.app), job_id)
    except KeyError:
        raise HTTPException(404, 'Job not found') from None
    account = await Users.get_user_by_id(result['job']['user_id'])
    result['job']['user_name'] = account.name if account else 'Deleted account'
    return result


@router.get('/versions')
async def versions(request: Request, path: str, user: UserModel = Depends(session)) -> dict:
    from . import evidence

    return await safe_call(evidence.versions(await service(request.app), user.id, path))


@router.get('/compare')
async def compare(
    request: Request,
    left: str,
    right: str,
    left_version: str = '',
    right_version: str = '',
    user: UserModel = Depends(session),
) -> dict:
    from . import evidence

    return await safe_call(
        evidence.compare(await service(request.app), user.id, left, right, left_version, right_version)
    )


@router.post('/events')
async def change_event(request: Request) -> dict:
    import hmac
    import json
    import os

    from .events import notify

    secret = os.environ.get('NEXTCLOUD_EVENT_SECRET', '')
    if not secret or not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + secret):
        raise HTTPException(401, 'Invalid event authentication')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, 'Event exceeds size limit')
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError('Expected an event object')
        count = notify(store(request.app), payload)
    except (ValueError, TypeError):
        raise HTTPException(400, 'Invalid event payload') from None
    return {'accepted': True, 'accounts_notified': count}


@router.get('/actions/tools')
async def action_tools(request: Request, user: UserModel = Depends(session)) -> dict:
    from .actions import mcp

    return {'tools': await safe_call(mcp(store(request.app).connection(user.id)))}


class ActionForm(BaseModel):
    operation: str = Field(max_length=100)
    arguments: dict = Field(default_factory=dict)


@router.post('/actions/read')
async def action_read(body: ActionForm, request: Request, user: UserModel = Depends(session)) -> dict:
    from .actions import read

    return await safe_call(read(store(request.app), user.id, body.operation, body.arguments))


@router.post('/actions/propose')
async def action_propose(body: ActionForm, request: Request, user: UserModel = Depends(session)) -> dict:
    from .actions import propose

    return await safe_call(propose(store(request.app), user.id, body.operation, body.arguments))


@router.get('/actions')
async def action_list(request: Request, user: UserModel = Depends(session)) -> dict:
    from .actions import listing

    return {'actions': listing(store(request.app), user.id)}


class ConfirmationForm(BaseModel):
    approve: bool


@router.post('/actions/{action_id}/confirm')
async def action_confirm(
    action_id: str, body: ConfirmationForm, request: Request, user: UserModel = Depends(session)
) -> dict:
    from .actions import confirm

    return await safe_call(confirm(store(request.app), user.id, action_id, body.approve))
