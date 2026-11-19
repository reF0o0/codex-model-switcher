import json

from test_switcher import CodexHomeFixture


class ModelCatalogTests(CodexHomeFixture):
    def cache(self, rows):
        (self.home/'models_cache.json').write_text(json.dumps({'models': rows}))

    def test_gpt_choices_follow_visible_cache_order_without_stale_defaults(self):
        self.cache([
            {'slug': 'gpt-6.2-sol', 'visibility': 'list',
             'supported_reasoning_levels': [{'effort': 'high'}, {'effort': 'ultra'}]},
            {'slug': 'gpt-reserve', 'visibility': 'hide'},
            {'slug': 'gpt-6.1-sol', 'visibility': 'hide'},
            {'slug': 'gpt-6.2-astra', 'visibility': 'list'},
            {'slug': 'codex-auto-review', 'visibility': 'hide'},
            {'slug': 'gpt-6.2-sol', 'visibility': 'list'},
        ])
        options = self.app.model_options()['openai']
        self.assertEqual([m['slug'] for m in options], ['gpt-6.2-sol', 'gpt-6.2-astra'])
        self.assertEqual(options[0]['efforts'], ['high', 'ultra'])

    def test_hidden_model_cannot_be_selected(self):
        self.cache([{'slug': 'gpt-6.2-sol', 'visibility': 'list'},
                    {'slug': 'gpt-reserve', 'visibility': 'hide'}])
        with self.assertRaises(ValueError):
            self.app.switch('openai', 'gpt-reserve', migrate=False)
        self.assertEqual(self.app.store.list(), [])

    def test_all_hidden_cache_does_not_restore_hidden_fallbacks(self):
        self.cache([{'slug': 'gpt-6.1-sol', 'visibility': 'hide'}])
        self.assertEqual(self.app.model_options()['openai'], [])

    def test_missing_or_corrupt_cache_prefers_62_sol(self):
        for content in [None, '{broken']:
            if content is not None:
                (self.home/'models_cache.json').write_text(content)
            options = self.app.model_options()['openai']
            self.assertEqual(options[0]['slug'], 'gpt-6.2-sol')

    def test_62_default_switch_preserves_history(self):
        self.cache([{'slug': 'gpt-6.2-sol', 'visibility': 'list',
                     'supported_reasoning_levels': [{'effort': 'high'}]}])
        routes = self.app.routes()
        before = (self.home/'sessions/one.jsonl').read_bytes()
        self.app.switch('openai', 'gpt-6.2-sol', migrate=False)
        self.assertEqual(self.app.runtime_status()['model'], 'gpt-6.2-sol')
        self.assertEqual(self.app.routes(), routes)
        self.assertEqual((self.home/'sessions/one.jsonl').read_bytes(), before)
