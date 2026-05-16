// Formatters referenced by dcc.Slider tooltips via tooltip.transform.
window.dccFunctions = window.dccFunctions || {};

// Milliseconds -> "m:ss" so position/start sliders read as clock time
// instead of a raw millisecond count.
window.dccFunctions.msClock = function (value) {
    var total = Math.round((value || 0) / 1000);
    var minutes = Math.floor(total / 60);
    var seconds = total % 60;
    return minutes + ":" + String(seconds).padStart(2, "0");
};
