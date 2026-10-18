"""Authenticated change notifications invalidate discovery, never grant source access."""

EVENTS = {
    'OCP\\Files\\Events\\Node\\NodeCreatedEvent',
    'OCP\\Files\\Events\\Node\\NodeWrittenEvent',
    'OCP\\Files\\Events\\Node\\NodeDeletedEvent',
    'OCP\\Files\\Events\\Node\\NodeRenamedEvent',
}


def notify(store, event: dict) -> int:
    if not isinstance(event.get('event'), dict) or event['event'].get('class') not in EVENTS:
        raise ValueError('Unsupported Nextcloud event')
    # An owner event can affect many share recipients. Rediscovery always runs as
    # each connected user; event-supplied paths and identities are not trusted ACLs.
    with store.db() as db:
        users = [row[0] for row in db.execute('SELECT user_id FROM connections')]
        for user_id in users:
            db.execute(
                'INSERT INTO index_notifications(user_id,revision) VALUES (?,1) '
                'ON CONFLICT(user_id) DO UPDATE SET revision=revision+1',
                (user_id,),
            )
    return len(users)


def dispatch(store) -> None:
    with store.db() as db:
        pending = db.execute(
            'SELECT user_id,revision FROM index_notifications WHERE user_id NOT IN '
            "(SELECT user_id FROM jobs WHERE action='index' AND status IN ('queued','processing'))"
        ).fetchall()
    for row in pending:
        try:
            store.enqueue(row['user_id'], 'index', {}, origin='event')
        except ValueError:
            # Disconnection can race a previously selected notification.
            continue
        with store.db() as db:
            db.execute(
                'DELETE FROM index_notifications WHERE user_id=? AND revision=?', (row['user_id'], row['revision'])
            )
