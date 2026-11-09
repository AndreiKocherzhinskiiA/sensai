from session.store import SessionStore


def test_previous_chat_uses_completed_messages_not_rename_or_empty_sessions(tmp_path):
    store = SessionStore(tmp_path/'sessions.db')
    for name in ('older', 'previous', 'empty', 'failed', 'current'):
        store.create_session(session_id=name)
    store.add_message('older', 'user', 'Older unrelated topic')
    store.add_message('previous', 'user', 'We chose SQLite and Friday exports.')
    store.add_message('previous', 'assistant', 'The export will be CSV.')
    store.add_message('failed', 'user', 'Failed turn must not count', status='failed')
    store.rename_session('older', 'Renamed most recently')
    store.add_message('current', 'user', 'Current chat must be excluded')
    result = store.previous_conversation('current')
    assert result['conversation']['id'] == 'previous'
    assert [m['content'] for m in result['messages']] == ['We chose SQLite and Friday exports.', 'The export will be CSV.']
    assert result['total_messages'] == 2
    assert not result['truncated']


def test_missing_cleared_and_deleted_history_is_explicit(tmp_path):
    store = SessionStore(tmp_path/'sessions.db')
    store.create_session(session_id='current')
    assert store.previous_conversation('current')['status'] == 'not_found'
    store.create_session(session_id='prior')
    store.add_message('prior', 'user', 'A saved conversation')
    store.clear_session_messages('prior')
    assert store.previous_conversation('current')['status'] == 'not_found'
    store.add_message('prior', 'user', 'Another message')
    store.delete_session('prior')
    result = store.previous_conversation('current')
    assert result['messages'] == []
    assert 'does not establish' in result['notice']


def test_long_previous_chat_declares_omissions_and_partial_messages(tmp_path):
    store = SessionStore(tmp_path/'sessions.db')
    store.create_session(session_id='prior')
    store.create_session(session_id='current')
    for n in range(30):
        store.add_message('prior', 'user', f'Message {n}: '+ 'long text '*100)
    result = store.previous_conversation('current', max_messages=4, max_chars=1500)
    assert sum(len(m['content']) for m in result['messages']) <= 1500
    assert result['total_messages'] == 30
    assert result['omitted_messages'] > 0
    assert result['truncated']
    assert any(m['content_truncated'] for m in result['messages'])


def test_reopened_store_keeps_the_previous_conversation(tmp_path):
    path = tmp_path/'sessions.db'
    store = SessionStore(path)
    store.create_session(session_id='prior')
    store.create_session(session_id='current')
    store.add_message('prior', 'user', 'Survives a restart')
    result = SessionStore(path).previous_conversation('current')
    assert result['messages'][0]['content'] == 'Survives a restart'
