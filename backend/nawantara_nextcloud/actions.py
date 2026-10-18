"""Per-user Context Agent calls; writes require a separate human confirmation endpoint."""

import json
import time
import uuid

READS = {'list_calendars', 'list_tasks', 'find_free_time_slot_in_calendar'}
WRITES = {'create_folder', 'add_task', 'schedule_event', 'complete_task', 'update_task'}


async def mcp(account: dict, operation: str | None = None, arguments: dict | None = None):
    from open_webui.utils.mcp.client import MCPClient

    client = MCPClient(server_name='Nextcloud Context Agent')
    try:
        await client.connect(
            account['instance'].rstrip('/') + '/index.php/apps/app_api/proxy/context_agent/mcp/',
            headers={'Authorization': 'Bearer ' + account['password']},
        )
        specs = await client.list_tool_specs()
        selected = [tool for tool in specs if tool['name'] in READS | WRITES]
        if operation is None:
            return [{**tool, 'requires_confirmation': tool['name'] in WRITES} for tool in selected]
        spec = next((tool for tool in selected if tool['name'] == operation), None)
        if spec is None:
            raise ValueError('This operation is unavailable for your Nextcloud account')
        from jsonschema import Draft202012Validator
        from referencing import Registry

        validator = Draft202012Validator(spec['parameters'], registry=Registry())
        if not validator.is_valid(arguments):
            raise ValueError('Arguments do not match the selected operation')
        return await client.call_tool(operation, arguments)
    except ValueError:
        raise
    except Exception:
        # Upstream exceptions may contain credential headers or source data.
        raise ValueError('Nextcloud action service unavailable or operation failed') from None
    finally:
        try:
            await client.disconnect()
        except Exception:
            # Cleanup errors must not disclose upstream request details.
            pass


async def read(store, user_id: str, operation: str, arguments: dict) -> dict:
    if operation not in READS:
        raise ValueError('This operation requires a separately confirmed proposal')
    account = store.connection(user_id)
    result = await mcp(account, operation, arguments)
    if store.connection(user_id)['generation'] != account['generation']:
        raise ValueError('Account connection changed')
    return {'result': result}


async def propose(store, user_id: str, operation: str, arguments: dict) -> dict:
    if operation not in WRITES:
        raise ValueError('Operation is not available for proposals')
    encoded = json.dumps(arguments)
    if len(encoded) > 16000:
        raise ValueError('Action arguments exceed size limit')
    account = store.connection(user_id)
    tools = await mcp(account)
    spec = next((tool for tool in tools if tool['name'] == operation), None)
    if spec is None:
        raise ValueError('Operation is unavailable for your Nextcloud account')
    from jsonschema import Draft202012Validator
    from referencing import Registry

    if not Draft202012Validator(spec['parameters'], registry=Registry()).is_valid(arguments):
        raise ValueError('Arguments do not match the selected operation')
    action_id = uuid.uuid4().hex
    with store.db() as db:
        db.execute(
            'INSERT INTO action_proposals VALUES (?,?,?,?,?,?,?,?)',
            (
                action_id,
                user_id,
                operation,
                store.cipher.encrypt(
                    json.dumps({'arguments': arguments, 'generation': account['generation']}).encode()
                ),
                'pending',
                store.cipher.encrypt(b'{}'),
                time.time(),
                time.time(),
            ),
        )
    return {
        'proposal_id': action_id,
        'status': 'pending',
        'review_url': '/nextcloud',
        'operation': operation,
        'arguments': arguments,
    }


def listing(store, user_id: str) -> list[dict]:
    with store.db() as db:
        rows = db.execute(
            'SELECT * FROM action_proposals WHERE user_id=? ORDER BY created DESC LIMIT 100', (user_id,)
        ).fetchall()
    return [
        {
            'id': row['id'],
            'operation': row['operation'],
            'status': row['status'],
            'created': row['created'],
            'arguments': json.loads(store.cipher.decrypt(row['payload']))['arguments'],
            'result': json.loads(store.cipher.decrypt(row['result'])),
        }
        for row in rows
    ]


def claim(store, user_id: str, action_id: str, approve: bool) -> tuple[dict, dict] | None:
    account = store.connection(user_id)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM action_proposals WHERE id=? AND user_id=?', (action_id, user_id)).fetchone()
        if row is None or row['status'] != 'pending':
            raise ValueError('Pending proposal unavailable')
        payload = json.loads(store.cipher.decrypt(row['payload']))
        if payload['generation'] != account['generation'] or time.time() - row['created'] > 3600:
            raise ValueError('Proposal expired; create a new proposal')
        db.execute(
            'UPDATE action_proposals SET status=?,updated=? WHERE id=?',
            (
                'executing' if approve else 'rejected',
                time.time(),
                action_id,
            ),
        )
    return (dict(row), payload) if approve else None


async def confirm(store, user_id: str, action_id: str, approve: bool) -> dict:
    selected = claim(store, user_id, action_id, approve)
    if selected is None:
        return {'status': 'rejected'}
    row, payload = selected
    try:
        account = store.connection(user_id)
        if account['generation'] != payload['generation'] or row['operation'] not in WRITES:
            raise ValueError('Account connection changed')
        result = await mcp(account, row['operation'], payload['arguments'])
        status = 'completed'
    except ValueError:
        status = 'uncertain'
        result = {'message': 'Check Nextcloud before creating another proposal. The operation may have executed.'}
    with store.db() as db:
        db.execute(
            'UPDATE action_proposals SET status=?,result=?,updated=? WHERE id=? AND user_id=?',
            (
                status,
                store.cipher.encrypt(json.dumps(result).encode()),
                time.time(),
                action_id,
                user_id,
            ),
        )
    return {'status': status, 'result': result}
