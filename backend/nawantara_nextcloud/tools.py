"""Native agent tools, using the authenticated caller's Nextcloud connection."""

from fastapi import Request
from open_webui.models.users import UserModel

from . import router


async def search_nextcloud(query: str, __request__: Request, __user__: dict) -> dict:
    """Find passages in indexed Nextcloud files you can currently read. Cite returned source URLs.

    :param query: A question or search phrase.
    """
    return await router.search(__request__, query, UserModel(**__user__))


async def list_nextcloud_files(path: str, __request__: Request, __user__: dict) -> dict:
    """List accessible Nextcloud files and folders to choose a source or save destination.

    :param path: Absolute folder path, for example /Documents.
    """
    return await router.files(__request__, path, UserModel(**__user__))


async def read_nextcloud_file(path: str, __request__: Request, __user__: dict) -> dict:
    """Read an accessible Nextcloud source and cite its URL.

    :param path: Absolute file path returned by file listing or search.
    """
    return await router.read(__request__, path, UserModel(**__user__))


async def save_nextcloud_text(path: str, text: str, __request__: Request, __user__: dict) -> dict:
    """Save findings only when explicitly requested for this destination.

    Never publish an entire private chat implicitly. Creates a new file without overwriting existing content.

    :param path: Exact new file path in the user's selected Nextcloud folder.
    :param text: Selected content to publish.
    """
    return await router.save(router.SaveForm(path=path, text=text), __request__, UserModel(**__user__))


async def save_nextcloud_attachment(path: str, file_id: str, __request__: Request, __user__: dict) -> dict:
    """Save an owned attachment only after an explicit request to publish it.

    Never automatically publish private uploads.

    :param path: Exact new file path in the user's selected Nextcloud folder.
    :param file_id: ID of the user's uploaded attachment.
    """
    return await router.save(router.SaveForm(path=path, file_id=file_id), __request__, UserModel(**__user__))


async def nextcloud_job_status(__request__: Request, __user__: dict) -> dict:
    """Check the current user's save and indexing jobs. A completed save does not imply that indexing has completed."""
    return await router.jobs(__request__, UserModel(**__user__))


TOOLS = [
    search_nextcloud,
    list_nextcloud_files,
    read_nextcloud_file,
    save_nextcloud_text,
    save_nextcloud_attachment,
    nextcloud_job_status,
]
