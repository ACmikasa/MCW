# EEG channel visibility design

## Intent

Add one channel selector beside the waveform controls. All channels remain received and processed from the LSL stream; only the channels selected in the selector are rendered.

## Goals

- Give users one clear, keyboard-accessible selector for the channels to display.
- Rebuild the waveform list so unselected channels are not shown.
- Preserve reception and buffering for every channel so showing it again displays the latest available samples.
- Leave the existing waveform layout, backend connection, and other dashboard areas unchanged.

## Non-goals and constraints

- Do not change the LSL stream or backend subscription.
- Do not discard samples for hidden channels; this request means hidden from the UI only.
- Do not add fatigue detection behavior or persistence of toggle state.

## Approach

Use a front-end `channelEnabled` state array. A multi-select control updates that state and rebuilds the waveform rows; `processEEGData()` continues filling every channel buffer exactly as before.

## Open implementation details

Use the existing chart control styles and a compact multi-select control with channel names and an accessible label. All channels are selected by default.
