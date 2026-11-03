# EEG Channel Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an independent display-only toggle before every EEG waveform while continuing to receive and buffer all channels.

**Architecture:** Keep channel visibility in a front-end `channelEnabled` array. `initChart()` creates an accessible toggle per channel; `drawChannel()` clears and skips hidden canvases; `processEEGData()` remains unchanged so all LSL samples continue to be received and buffered.

**Tech Stack:** Existing HTML, CSS, and browser JavaScript in `lsl_EEG/web/frontend.html`.

**Spec:** `docs/superpowers/specs/2026-11-03-channel-visibility-design.md`

## Global Constraints

- Do not change the LSL stream or backend subscription.
- Hidden channels remain received and buffered; only rendering is disabled.
- Do not add fatigue detection behavior or persistence of toggle state.

## Review Focus

- Initial state: every channel is visible and its button exposes the correct pressed state.
- Toggle behavior: one channel hides and restores without affecting neighboring channels.
- Hidden rendering: the canvas is cleared and remains blank while samples continue arriving.
- Accessibility: button has a label, title, and keyboard activation behavior.
- Responsive layout: the added control does not break the existing channel row.

### Task 1: Add display-only channel toggles

**Files:**
- Modify: `lsl_EEG/web/frontend.html` (channel styles, state, `initChart()`, `drawChannel()`, and toggle handler)

**Interfaces:**
- Produces `channelEnabled`, `toggleChannelVisibility(channelIndex)`, and button state updates used only by the existing chart renderer.

- [ ] Add `channelEnabled` initialized to `true` for `CONFIG.numChannels` and add a compact toggle button before each channel name in `initChart()`.
- [ ] Add accessible button text, `aria-pressed`, title, and CSS for visible/hidden states.
- [ ] Implement `toggleChannelVisibility(channelIndex)` to update state, clear the channel canvas when hidden, update button state, and redraw when shown.
- [ ] Make `drawChannel(channelIndex)` clear and return for hidden channels; leave `processEEGData()` buffering all channels.
- [ ] Run a browser smoke check with simulated EEG data: confirm all rows/buttons exist, click one toggle, verify only its canvas is blank and `dataBuffers` still grows, then click again and verify drawing resumes.
- [ ] Review the diff and ensure only the intended frontend and plan/spec files changed.
