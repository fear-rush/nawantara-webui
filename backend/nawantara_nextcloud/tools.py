"""Native agent tools, using the authenticated caller's Nextcloud connection."""

from fastapi import Request
from open_webui.models.users import UserModel

from . import router


async def list_nextcloud_files(path: str, __request__: Request, __user__: dict) -> dict:
    """List accessible Nextcloud files and folders to choose a source or save destination.

    :param path: Absolute folder path, for example /Documents.
    """
    return await router.files(__request__, path, UserModel(**__user__))


async def read_nextcloud_file(path: str, __request__: Request, __user__: dict) -> dict:
    """Read an accessible text file and cite its URL. Other formats open in Nextcloud.

    :param path: Absolute text file path returned by file listing.
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
    """Check the current user's save jobs. A queued save is not yet a saved file."""
    return await router.jobs(__request__, UserModel(**__user__))


async def list_nextcloud_actions(__request__: Request, __user__: dict) -> dict:
    """Discover the selected Nextcloud calendar, task and folder tools with their argument schemas."""
    return await router.action_tools(__request__, UserModel(**__user__))


async def read_nextcloud_action(operation: str, arguments: dict, __request__: Request, __user__: dict) -> dict:
    """Run a read-only calendar/task operation discovered with list_nextcloud_actions.

    :param operation: Read operation name from list_nextcloud_actions.
    :param arguments: Arguments matching that operation's schema.
    """
    return await router.action_read(
        router.ActionForm(operation=operation, arguments=arguments), __request__, UserModel(**__user__)
    )


async def propose_nextcloud_action(operation: str, arguments: dict, __request__: Request, __user__: dict) -> dict:
    """Prepare a requested Nextcloud action for the user to review in Nextcloud > Actions.

    This DOES NOT execute the operation. Only the user can approve it in the interface.
    :param operation: Write operation name from list_nextcloud_actions.
    :param arguments: Complete proposed arguments matching the operation's schema.
    """
    return await router.action_propose(
        router.ActionForm(operation=operation, arguments=arguments), __request__, UserModel(**__user__)
    )


TOOLS = [
    list_nextcloud_actions,
    read_nextcloud_action,
    propose_nextcloud_action,
    list_nextcloud_files,
    read_nextcloud_file,
    save_nextcloud_text,
    save_nextcloud_attachment,
    nextcloud_job_status,
]
