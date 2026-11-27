"""Separate role budget, credentials, transport and failure policy; offline."""
from contextlib import ExitStack
import os
import types
import unittest
from unittest.mock import patch, MagicMock

from test_customer_worker import load_gateway


class InterpretationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.gateway = load_gateway(self.stack)
        self.stack.enter_context(patch.dict(os.environ, {'OPENROUTER_API_KEY': 'sk-customer-public-fixture'}))
        self.schema = types.SimpleNamespace(model_json_schema=lambda: {'type': 'object'}, model_validate=lambda x: x)

    def test_role_uses_customer_key_nemotron_budget_and_no_fallback(self):
        initial = self.gateway.configuration_snapshot()
        with patch.object(self.gateway, '_openrouter', return_value=({}, {'finish_reason': 'stop'})) as call:
            self.gateway.structured(self.schema, 'fixture', purpose='claim_interpretation', examples=[({}, {})])
            settings, key, model = call.call_args.args[:3]
            self.assertEqual(key, 'sk-customer-public-fixture')
            self.assertEqual(model, 'nvidia/nemotron-3.5-super-vl:free')
            self.assertEqual(settings.max_output_tokens, 2048)
            self.assertFalse(settings.allow_fallbacks)
            self.assertTrue(call.call_args.kwargs['json_mode'])
            self.assertEqual(call.call_args.kwargs['reasoning'], {'enabled': False})
            self.assertEqual(call.call_args.kwargs['examples'], [({}, {})])
            self.assertEqual(self.gateway.last_call()['purpose'], 'claim_interpretation')
        self.assertEqual(self.gateway.configuration_snapshot(), initial)

    def test_nemotron_text_stages_disable_optional_reasoning(self):
        for purpose in ('table_selection', 'text_verification', 'footnote_followup'):
            with self.subTest(purpose=purpose), \
                    patch.object(self.gateway, '_openrouter', return_value=({}, {})) as call:
                self.gateway.structured(self.schema, 'fixture', purpose=purpose,
                                        model='nvidia/nemotron-3.5-super-vl:free')
                self.assertEqual(call.call_args.kwargs['reasoning'], {'enabled': False})
                self.assertFalse(call.call_args.args[0].allow_fallbacks)

    def test_transient_failure_is_not_retried_or_given_to_another_model(self):
        error = self.gateway.ModelServiceError('Provider temporarily unavailable', code=503)
        with patch.object(self.gateway, '_openrouter', side_effect=error) as call:
            with self.assertRaises(self.gateway.ModelServiceError):
                self.gateway.structured(self.schema, 'fixture', purpose='claim_interpretation')
            self.assertEqual(call.call_count, 1)

    def test_reasoning_and_vision_still_use_existing_models(self):
        with patch.object(self.gateway, '_openrouter', return_value=({}, {})) as call:
            self.gateway.structured(self.schema, 'fixture')
            self.assertEqual(call.call_args.args[2], 'vendor/text-model')
            self.assertNotIn('json_mode', call.call_args.kwargs)
            self.gateway.structured(self.schema, 'fixture', task='vision')
            self.assertEqual(call.call_args.args[2], 'vendor/vision-model')

    def test_text_specialist_disables_configured_fallbacks(self):
        model = "baidu/ernie-5.5-flash-preview:free"
        with patch.object(self.gateway, "_openrouter", return_value=({}, {})) as call:
            self.gateway.structured(self.schema, "fixture", purpose="text_verification", model=model)
        self.assertEqual(call.call_args.args[2], model)
        self.assertFalse(call.call_args.args[0].allow_fallbacks)
        self.assertNotIn('reasoning', call.call_args.kwargs)

    def test_json_mode_has_fewshots_and_local_schema_is_still_checked(self):
        response = types.SimpleNamespace(status_code=200, is_error=False, headers={}, json=lambda: {
            'id': 'fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]})
        self.gateway.httpx.Timeout = lambda *a, **k: 10
        self.gateway.httpx.post.return_value = response
        self.gateway.httpx.post.side_effect = None
        self.gateway._openrouter(self.gateway.config(), 'sk-customer-public-fixture', self.gateway.INTERPRETATION_MODEL,
                                 'fixture', 'system', {}, [], 0, json_mode=True, examples=[({'input': 1}, {'status': 'ready'})])
        payload = self.gateway.httpx.post.call_args.kwargs['json']
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual([m['role'] for m in payload['messages']], ['system', 'user', 'assistant', 'user'])
        validator = MagicMock()
        validator.validate.side_effect = ValueError('Invalid structure')
        with patch.object(self.gateway, 'Draft202012Validator', return_value=validator), \
             patch.object(self.gateway, '_openrouter', return_value=({}, {})):
            with self.assertRaises(self.gateway.ModelServiceError):
                self.gateway.structured(self.schema, 'fixture', purpose='claim_interpretation')


if __name__ == '__main__':
    unittest.main()
