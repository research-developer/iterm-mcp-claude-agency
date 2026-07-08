/**
 * ControIDE gamepad mapping logic (pure, DOM-free).
 *
 * Maps standard-mapping Gamepad API samples to abstract actions
 * ("move_prev" / "move_next" / "select" / "cancel") with rising-edge
 * button detection and stick hysteresis, mirroring
 * core/driver.py:GamepadController so both sides stay in lock-step.
 *
 * Loaded by driver.html as a plain script (exposes window.GamepadNav)
 * and by node --test via require() (exposes module.exports). driver.js
 * owns the requestAnimationFrame loop and DOM wiring; nothing in this
 * file touches the DOM, so it stays testable headlessly.
 */

(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.GamepadNav = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Standard Gamepad API mapping (https://w3.org/TR/gamepad/#remapping)
  const BUTTON_ACTIONS = {
    0: "select", // A / cross
    1: "cancel", // B / circle
    12: "move_prev", // D-pad up
    13: "move_next", // D-pad down
  };
  const MAPPED_BUTTONS = [0, 1, 12, 13];
  const STICK_AXIS = 1; // left stick Y
  const AXIS_PRESS_THRESHOLD = 0.6;
  const AXIS_RELEASE_THRESHOLD = 0.3;

  /**
   * Create a stateful mapper for one gamepad.
   *
   * handleEvent takes {type:"button",index,pressed} or
   * {type:"axis",index,value} and returns an action string on a rising
   * edge, or null for holds, releases, unmapped inputs, and malformed
   * events.
   */
  function createMapper() {
    const pressedButtons = new Set();
    let axisDirection = 0; // -1 (up), 0 (neutral), +1 (down)

    function handleButton(event) {
      const index = event.index;
      if (typeof index !== "number") return null;
      if (!event.pressed) {
        pressedButtons.delete(index);
        return null;
      }
      if (pressedButtons.has(index)) return null; // held — already fired
      pressedButtons.add(index);
      return BUTTON_ACTIONS.hasOwnProperty(index)
        ? BUTTON_ACTIONS[index]
        : null;
    }

    function handleAxis(event) {
      if (event.index !== STICK_AXIS) return null;
      const value = event.value;
      if (typeof value !== "number" || isNaN(value)) return null;
      let direction = 0;
      if (value <= -AXIS_PRESS_THRESHOLD) {
        direction = -1;
      } else if (value >= AXIS_PRESS_THRESHOLD) {
        direction = 1;
      } else if (Math.abs(value) >= AXIS_RELEASE_THRESHOLD) {
        direction = axisDirection; // hysteresis band: hold state
      }
      if (direction === axisDirection) return null;
      axisDirection = direction;
      if (direction === -1) return "move_prev";
      if (direction === 1) return "move_next";
      return null; // returned to neutral
    }

    return {
      handleEvent: function (event) {
        if (!event) return null;
        if (event.type === "button") return handleButton(event);
        if (event.type === "axis") return handleAxis(event);
        return null;
      },
    };
  }

  /**
   * Sample one Gamepad-like object ({buttons:[{pressed}], axes:[float]})
   * through a mapper and return the actions fired this frame, in a
   * stable order (buttons first, then stick).
   */
  function pollPad(mapper, pad) {
    const actions = [];
    MAPPED_BUTTONS.forEach(function (index) {
      const btn = pad.buttons && pad.buttons[index];
      const action = mapper.handleEvent({
        type: "button",
        index: index,
        pressed: !!(btn && btn.pressed),
      });
      if (action) actions.push(action);
    });
    const value = pad.axes && pad.axes[STICK_AXIS];
    const action = mapper.handleEvent({
      type: "axis",
      index: STICK_AXIS,
      value: typeof value === "number" ? value : 0,
    });
    if (action) actions.push(action);
    return actions;
  }

  return {
    createMapper: createMapper,
    pollPad: pollPad,
    BUTTON_ACTIONS: BUTTON_ACTIONS,
    STICK_AXIS: STICK_AXIS,
    AXIS_PRESS_THRESHOLD: AXIS_PRESS_THRESHOLD,
    AXIS_RELEASE_THRESHOLD: AXIS_RELEASE_THRESHOLD,
  };
});
