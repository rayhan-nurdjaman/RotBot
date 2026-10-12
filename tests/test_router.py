import asyncio
import json
import logging

import httpx
import pytest

import router
from instance import Instance
from models import DEFAULT_MODEL, parse_model


def answer(choice, probabilities=None, cost=0.00002):
    return {
        'model': 'typesafe/jev-1.13-20260917',
        'answers': {'model': {'type': 'choice', 'choice': choice, 'probabilities': probabilities or {choice: 1}, 'confidence': 1}},
        'usage': {'input_tokens': 400, 'output_tokens': 49, 'cost': cost},
    }


def use_jev(monkeypatch, handler):
    """Replaces the HTTP client with one that answers from `handler(request)`. Returns the list of requests sent."""
    sent = []

    def record(request):
        sent.append(request)
        return handler(request)

    monkeypatch.setattr(router, 'http', httpx.AsyncClient(transport=httpx.MockTransport(record)))
    return sent


def route(instance):
    return asyncio.run(router.route(instance))


def chat_with(*texts):
    instance = Instance('chat-1')
    for text in texts:
        instance.add_user_message(text)
    return instance


def test_haiku_answer_routes_to_haiku(monkeypatch):
    use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('haiku-5.5-low')))
    assert route(chat_with('hey')) == parse_model('haiku-5.5-low')


def test_sonnet_answer_routes_to_sonnet(monkeypatch):
    use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('sonnet-5.5-medium')))
    assert route(chat_with('plan my week')) == parse_model('sonnet-5.5-medium')


def test_request_asks_jev_to_choose_between_the_two_candidates(monkeypatch):
    sent = use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('haiku-5.5-low')))
    route(chat_with('hey'))

    assert str(sent[0].url) == 'https://openrouter.ai/api/v1/systemone'
    body = json.loads(sent[0].content)
    assert body['model'] == 'typesafe/jev-1.13'
    question = body['questions']['model']
    assert question['type'] == 'choice'
    assert set(question['criteria']) == {'haiku-5.5-low', 'sonnet-5.5-medium'}
    assert all(parse_model(name) for name in question['criteria'])


def test_state_is_the_last_messages_as_text_with_images_marked(monkeypatch):
    sent = use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('sonnet-5.5-medium')))
    instance = Instance('chat-1')
    instance.add_user_message('what time are the talks?', image_url='data:image/jpeg;base64,AAAA')
    instance.add_assistant_message('Looks like 10am.')
    instance.add_to_context({'role': 'tool', 'tool_call_id': 'x', 'content': 'TOOL RESULT'})
    route(instance)

    state = json.loads(sent[0].content)['state']
    assert 'what time are the talks?' in state and '[image attached]' in state
    assert 'assistant: Looks like 10am.' in state
    assert 'AAAA' not in state and 'TOOL RESULT' not in state


def test_state_keeps_only_the_recent_messages_and_cuts_long_ones(monkeypatch):
    sent = use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('haiku-5.5-low')))
    instance = chat_with(*[f'message number {n}' for n in range(10)])
    instance.add_user_message('x' * 5000)
    route(instance)

    state = json.loads(sent[0].content)['state']
    assert len(state.split('\n')) == router.HISTORY_MESSAGES
    assert 'message number 0' not in state
    assert len(state.split('\n')[-1]) == len('user: ') + router.MAX_CHARS


def test_route_does_not_change_the_instance(monkeypatch):
    use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('haiku-5.5-low')))
    instance = chat_with('hey', 'again')
    before = list(instance.context_window)
    route(instance)
    assert instance.context_window == before


@pytest.mark.parametrize('handler', [
    lambda request: httpx.Response(500, text='boom'),
    lambda request: httpx.Response(401, json={'error': 'no'}),
    lambda request: httpx.Response(200, text='not json'),
    lambda request: httpx.Response(200, json={'answers': {}}),
    lambda request: httpx.Response(200, json=answer('opus-5.5-max')),
    lambda request: httpx.Response(200, json=answer('nonsense')),
    lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('slow')),
], ids=['http-500', 'http-401', 'not-json', 'no-answer', 'not-a-candidate', 'unknown-model', 'timeout'])
def test_any_failure_uses_the_cheapest_model_and_warns(monkeypatch, caplog, handler):
    use_jev(monkeypatch, handler)
    with caplog.at_level(logging.WARNING):
        assert route(chat_with('hey')) == DEFAULT_MODEL
    assert 'Jev routing failed' in caplog.text


def test_timeout_comes_from_the_config(monkeypatch):
    sent = use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer('haiku-5.5-low')))
    route(chat_with('hey'))
    assert sent[0].extensions['timeout']['read'] == router.config['jev_timeout_s']


def test_success_logs_choice_probabilities_and_cost(monkeypatch, caplog):
    use_jev(monkeypatch, lambda request: httpx.Response(200, json=answer(
        'sonnet-5.5-medium', {'haiku-5.5-low': 0.04, 'sonnet-5.5-medium': 0.96}, cost=0.0000171)))
    with caplog.at_level(logging.INFO):
        route(chat_with('hey'))

    line = next(record.getMessage() for record in caplog.records if record.getMessage().startswith('jev_call '))
    data = json.loads(line[len('jev_call '):])
    assert data['chat_id'] == 'chat-1'
    assert data['choice'] == 'sonnet-5.5-medium'
    assert data['probabilities'] == {'haiku-5.5-low': 0.04, 'sonnet-5.5-medium': 0.96}
    assert data['cost'] == 0.0000171
    assert 'latency_s' in data


def test_auth_header_is_the_openrouter_key():
    assert router.http.headers['authorization'] == 'Bearer test-key'
