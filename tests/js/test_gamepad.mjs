/**
 * Node-level tests for the pure gamepad-mapping logic in static/gamepad.js.
 *
 * Run directly:            node --test tests/js/
 * Run via the CI suite:    tests/test_gamepad_js.py (skips if node is absent)
 *
 * These mirror tests/test_gamepad_controller.py so the browser and Python
 * implementations of the mapping stay in lock-step.
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const GamepadNav = require("../../static/gamepad.js");

function button(index, pressed) {
  return { type: "button", index: index, pressed: pressed };
}

function axis(index, value) {
  return { type: "axis", index: index, value: value };
}

// ── Button mapping ──────────────────────────────────────────────────────────

test("dpad up maps to move_prev", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(12, true)), "move_prev");
});

test("dpad down maps to move_next", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(13, true)), "move_next");
});

test("face A maps to select", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(0, true)), "select");
});

test("face B maps to cancel", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(1, true)), "cancel");
});

test("unmapped button returns null", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(7, true)), null);
});

test("button release returns null", () => {
  const m = GamepadNav.createMapper();
  m.handleEvent(button(0, true));
  assert.equal(m.handleEvent(button(0, false)), null);
});

// ── Edge detection ──────────────────────────────────────────────────────────

test("held button fires once", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(0, true)), "select");
  assert.equal(m.handleEvent(button(0, true)), null);
  assert.equal(m.handleEvent(button(0, true)), null);
});

test("release re-arms button", () => {
  const m = GamepadNav.createMapper();
  m.handleEvent(button(13, true));
  m.handleEvent(button(13, false));
  assert.equal(m.handleEvent(button(13, true)), "move_next");
});

test("buttons tracked independently", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(button(12, true)), "move_prev");
  assert.equal(m.handleEvent(button(13, true)), "move_next");
  assert.equal(m.handleEvent(button(12, true)), null);
});

// ── Left-stick Y axis with hysteresis ───────────────────────────────────────

test("stick up maps to move_prev", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(1, -0.9)), "move_prev");
});

test("stick down maps to move_next", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(1, 0.9)), "move_next");
});

test("below press threshold returns null", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(1, -0.4)), null);
});

test("held deflection fires once", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(1, 0.9)), "move_next");
  assert.equal(m.handleEvent(axis(1, 0.9)), null);
  assert.equal(m.handleEvent(axis(1, 0.8)), null);
});

test("must return to neutral to re-arm", () => {
  const m = GamepadNav.createMapper();
  m.handleEvent(axis(1, 0.9));
  m.handleEvent(axis(1, 0.4)); // hysteresis band — still deflected
  assert.equal(m.handleEvent(axis(1, 0.9)), null);
  m.handleEvent(axis(1, 0.1)); // true neutral re-arms
  assert.equal(m.handleEvent(axis(1, 0.9)), "move_next");
});

test("direction flip fires without passing neutral", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(1, 0.9)), "move_next");
  assert.equal(m.handleEvent(axis(1, -0.9)), "move_prev");
});

test("other axes ignored", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent(axis(0, -0.9)), null);
  assert.equal(m.handleEvent(axis(3, 0.9)), null);
});

// ── Malformed events ────────────────────────────────────────────────────────

test("empty event returns null", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent({}), null);
});

test("unknown type returns null", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent({ type: "touchpad", x: 0.5 }), null);
});

test("non-numeric axis value returns null", () => {
  const m = GamepadNav.createMapper();
  assert.equal(m.handleEvent({ type: "axis", index: 1, value: "up" }), null);
});

// ── pollPad: sampling a Gamepad-like snapshot ───────────────────────────────

function fakePad(pressedIndices, axes) {
  const buttons = [];
  for (let i = 0; i < 17; i++) {
    buttons.push({ pressed: pressedIndices.includes(i) });
  }
  return { buttons: buttons, axes: axes || [0, 0, 0, 0] };
}

test("pollPad emits actions for fresh presses", () => {
  const m = GamepadNav.createMapper();
  const actions = GamepadNav.pollPad(m, fakePad([0]));
  assert.deepEqual(actions, ["select"]);
});

test("pollPad emits nothing while a button is held", () => {
  const m = GamepadNav.createMapper();
  GamepadNav.pollPad(m, fakePad([13]));
  assert.deepEqual(GamepadNav.pollPad(m, fakePad([13])), []);
  GamepadNav.pollPad(m, fakePad([]));
  assert.deepEqual(GamepadNav.pollPad(m, fakePad([13])), ["move_next"]);
});

test("pollPad reads left-stick Y from axes", () => {
  const m = GamepadNav.createMapper();
  assert.deepEqual(GamepadNav.pollPad(m, fakePad([], [0, -0.9, 0, 0])), [
    "move_prev",
  ]);
  assert.deepEqual(GamepadNav.pollPad(m, fakePad([], [0, -0.9, 0, 0])), []);
});

test("pollPad tolerates a pad with missing axes", () => {
  const m = GamepadNav.createMapper();
  const pad = { buttons: [{ pressed: true }], axes: [] };
  assert.deepEqual(GamepadNav.pollPad(m, pad), ["select"]);
});
