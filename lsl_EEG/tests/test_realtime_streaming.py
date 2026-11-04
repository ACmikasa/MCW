from __future__ import annotations

import unittest
import sys
from types import SimpleNamespace

import numpy as np

# The acquisition module imports pylsl at module load time. Unit tests exercise
# only its buffer helpers and should not require EEG hardware dependencies.
sys.modules.setdefault(
    "pylsl",
    SimpleNamespace(StreamInlet=object, resolve_streams=lambda wait_time=0: []),
)

from acquisition.lsl_receiver import LSLReceiver
from app.realtime_stream import sse_event


class RealtimeStreamingTests(unittest.TestCase):
    def test_combine_chunks_builds_one_contiguous_display_frame(self) -> None:
        chunks = [
            (4, np.array([[1.0, 2.0], [10.0, 20.0]]), np.array([0.0, 0.1]), 20),
            (5, np.array([[3.0], [30.0]]), np.array([0.2]), 22),
        ]

        sequence, eeg, timestamps, first_index = LSLReceiver.combine_chunks(chunks)

        self.assertEqual(sequence, 5)
        self.assertEqual(first_index, 20)
        np.testing.assert_array_equal(eeg, [[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]])
        np.testing.assert_array_equal(timestamps, [0.0, 0.1, 0.2])

    def test_sse_event_is_compact_and_reconnectable(self) -> None:
        encoded = sse_event("eeg", {"eeg": [[1.0]], "label": "脑电"}, 17)

        self.assertTrue(encoded.startswith("id: 17\nevent: eeg\ndata: "))
        self.assertIn('"label":"脑电"', encoded)
        self.assertTrue(encoded.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
