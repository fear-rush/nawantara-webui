"""Session-authenticated Nextcloud connection, document and job endpoints."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.models.users import UserModel
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel, Field

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
async def search(request: Request, query: str, user: UserModel = Depends(session)) -> dict:
    api = await service(request.app)
    return await safe_call(api.search(user.id, query))


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
    store(request.app).control(job_id, user.id, body.operation)
    return {'accepted': True}


@router.get('/admin/jobs')
async def admin_jobs(request: Request, user: UserModel = Depends(get_admin_user)) -> dict:
    return {'jobs': store(request.app).jobs(None)}
