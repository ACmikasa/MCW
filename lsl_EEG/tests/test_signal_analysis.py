from __future__ import annotations

import unittest

import numpy as np

from data.schemas import EEGChunk
from processing.signal_analysis import analyze_signal, bandpass_filter, preview_chunk


class SignalAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        rate = 200.0
        time = np.arange(0, 4, 1 / rate)
        samples = np.vstack([np.sin(2 * np.pi * 10 * time) + 0.2 * np.sin(2 * np.pi * 60 * time)])
        self.chunk = EEGChunk(samples, time, rate, ["Cz"])

    def test_analysis_returns_all_visualizations(self) -> None:
        result = analyze_signal(self.chunk, 0, 1, 45, 50)
        self.assertEqual(result["metadata"]["channel_label"], "Cz")
        self.assertTrue(result["waveform"]["filtered"])
        self.assertTrue(result["fft"]["frequency"])
        self.assertTrue(result["psd"]["power_db"])
        self.assertTrue(result["spectrogram"]["power_db"])
        self.assertIn("score", result["detection"])

    def test_filter_validation_and_preview(self) -> None:
        with self.assertRaises(ValueError):
            bandpass_filter(self.chunk.samples[0], 200, 60, 110)
        preview = preview_chunk(self.chunk, 100)
        self.assertLessEqual(len(preview["timestamps"]), 100)
        self.assertEqual(preview["channel_labels"], ["Cz"])


if __name__ == "__main__":
    unittest.main()
