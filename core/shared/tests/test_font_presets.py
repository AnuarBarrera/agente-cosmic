class TestChooseFontPreset:
    def test_same_seed_always_returns_same_preset(self):
        from core.shared.font_presets import choose_font_preset
        first = choose_font_preset('job-123')
        second = choose_font_preset('job-123')
        assert first == second

    def test_empty_seed_does_not_raise(self):
        from core.shared.font_presets import choose_font_preset, FONT_PRESETS
        result = choose_font_preset('')
        assert result in FONT_PRESETS

    def test_different_seeds_can_return_different_presets(self):
        from core.shared.font_presets import choose_font_preset
        seeds = [f'job-{i}' for i in range(20)]
        results = {choose_font_preset(s)['font_family'] for s in seeds}
        assert len(results) > 1
