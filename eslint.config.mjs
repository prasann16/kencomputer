// One check for the chat page (web/): every name it uses exists. A name that doesn't (a variable renamed away)
// isn't a syntax error, so it only breaks the page when that part is drawn. tests/check.sh runs this.
const browser = ["AbortSignal", "addEventListener", "Blob", "cancelAnimationFrame", "clearInterval", "clearTimeout", "console",
  "crypto", "CSS", "document", "Event", "fetch", "File", "FormData", "history", "innerWidth", "innerHeight", "localStorage",
  "location", "MediaRecorder", "navigator", "removeEventListener", "requestAnimationFrame", "sessionStorage", "setInterval",
  "setTimeout", "URL", "URLSearchParams", "WebSocket", "window", "matchMedia", "getComputedStyle", "Image", "Audio",
  "AudioContext", "TextDecoder", "TextEncoder", "Response", "Request", "Headers", "performance", "queueMicrotask",
  "structuredClone", "atob", "btoa", "alert", "confirm", "prompt", "open", "close", "scrollTo", "devicePixelRatio",
  "ResizeObserver", "IntersectionObserver", "MutationObserver", "AbortController", "Notification", "visualViewport"];
export default [{
  files: ["web/*.js"],
  languageOptions: { sourceType: "module", ecmaVersion: "latest", globals: Object.fromEntries(browser.map((n) => [n, "readonly"])) },
  rules: { "no-undef": "error" },
}];
