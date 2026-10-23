import React, { useLayoutEffect, useRef } from "react";

/** Keep long form text in the page's scroll flow, including on Chrome 86. */
export function AutoTextarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const field = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    const measure = document.createElement("textarea");
    measure.setAttribute("aria-hidden", "true");
    measure.tabIndex = -1;
    document.body.appendChild(measure);
    function fit() {
      if (!element) return;
      const style = getComputedStyle(element);
      for (const property of ["width", "box-sizing", "font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing", "word-spacing", "text-indent", "text-transform", "padding-top", "padding-bottom", "padding-left", "padding-right", "border-top-width", "border-bottom-width", "border-left-width", "border-right-width", "border-style", "tab-size"])
        measure.style.setProperty(property, style.getPropertyValue(property));
      Object.assign(measure.style, { position: "fixed", left: "-10000px", top: "0", visibility: "hidden", pointerEvents: "none", height: "auto", minHeight: "0", maxHeight: "none", overflow: "hidden" });
      measure.rows = props.rows ?? 2;
      measure.wrap = element.wrap;
      measure.value = element.value;
      // Measure outside the page flow. Temporarily shrinking the real field
      // would clamp the page's scroll position before its height is restored.
      element.style.height = Math.ceil(measure.scrollHeight + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)) + "px";
    }
    fit();
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== width) {
        width = element.clientWidth;
        fit();
      }
    });
    observer.observe(element);
    return () => { observer.disconnect(); measure.remove(); };
  }, [props.value, props.rows]);
  return <textarea {...props} ref={field} className={"of-auto-textarea " + (props.className ?? "")} />;
}
