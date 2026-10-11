import asyncio

import pytest

import db


def run(coro):
    return asyncio.run(coro)


async def user_version(path):
    import aiosqlite
    async with aiosqlite.connect(path) as conn:
        async with conn.execute('PRAGMA user_version') as cursor:
            return (await cursor.fetchone())[0]


async def tables(path):
    import aiosqlite
    async with aiosqlite.connect(path) as conn:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            return {row[0] for row in await cursor.fetchall()}


def test_init_creates_the_folder_the_file_and_the_schema(tmp_path):
    path = str(tmp_path / 'nested' / 'rotbot.db')

    async def go():
        await db.init(path)
        await db.close()

    run(go())

    assert run(user_version(path)) == len(db.MIGRATIONS)
    assert 'reminders' in run(tables(path))


def test_opening_twice_is_idempotent_and_keeps_data(tmp_path):
    path = str(tmp_path / 'rotbot.db')

    async def go():
        await db.init(path)
        await db.connection().execute(
            "INSERT INTO reminders (chat_id, description, due_at, attempt_at, created_at) VALUES (1, 'x', 1, 1, 1)")
        await db.connection().commit()
        await db.close()
        await db.init(path)
        async with db.connection().execute('SELECT COUNT(*) FROM reminders') as cursor:
            count = (await cursor.fetchone())[0]
        await db.close()
        return count

    assert run(go()) == 1


def test_connection_before_init_is_an_error():
    with pytest.raises(RuntimeError):
        db.connection()


def test_a_database_from_a_newer_version_is_refused(tmp_path):
    path = str(tmp_path / 'rotbot.db')

    async def go():
        import aiosqlite
        async with aiosqlite.connect(path) as conn:
            await conn.execute(f'PRAGMA user_version = {len(db.MIGRATIONS) + 1}')
            await conn.commit()
        await db.init(path)

    with pytest.raises(RuntimeError, match='newer'):
        run(go())
    with pytest.raises(RuntimeError):
        db.connection()


def test_a_failing_migration_leaves_the_database_unchanged_and_the_next_start_retries(tmp_path, monkeypatch):
    path = str(tmp_path / 'rotbot.db')
    good = db.MIGRATIONS[0]
    monkeypatch.setattr(db, 'MIGRATIONS', [good, 'CREATE TABLE a (id INTEGER); THIS IS NOT SQL;'])

    with pytest.raises(Exception):
        run(db.init(path))
    with pytest.raises(RuntimeError):
        db.connection()

    assert run(user_version(path)) == 1
    assert 'a' not in run(tables(path))

    monkeypatch.setattr(db, 'MIGRATIONS', [good, 'CREATE TABLE a (id INTEGER);'])
    run(db.init(path))
    run(db.close())

    assert run(user_version(path)) == 2
    assert 'a' in run(tables(path))
