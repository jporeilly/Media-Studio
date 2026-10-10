/**
 * The inline text box: a textarea laid exactly over a Text or Callout shape, in the renderer's own font at
 * the current zoom, so what is typed sits where it will be drawn. Click away (a pointer-down on the canvas,
 * or any blur) commits; Esc puts the old text back; Ctrl+Enter commits too; Ctrl+S commits and saves (its
 * default prevented, so the browser's "Save page" never opens). Its keys never reach the editor's tool keys.
 */
import { useEffect, useRef } from "react";
import { type CalloutShape, type TextShape, LINE_HEIGHT, MAX_TEXT_CHARS, TEXT_PADDING, TEXT_PX, UI_FONT, textOn } from "../../lib/annotate";

export function TextBox({ shape, zoom, onChange, onCommit, onCommitAndSave, onCancel }: {
  shape: TextShape | CalloutShape;
  zoom: number;
  onChange: (text: string) => void;
  onCommit: () => void;
  onCommitAndSave: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, [shape.id]);
  const px = TEXT_PX[shape.text_size] * zoom;
  const colour = shape.type === "callout" ? textOn(shape.colour) : shape.colour;
  return (
    <textarea
      ref={ref}
      data-annotate-text={shape.id}
      aria-label="Text"
      value={shape.text}
      maxLength={MAX_TEXT_CHARS}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") { e.preventDefault(); onCancel(); return; }
        if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); onCommit(); return; }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); onCommitAndSave(); }
      }}
      onPointerDown={(e) => e.stopPropagation()}
      style={{
        position: "absolute",
        left: shape.x * zoom,
        top: shape.y * zoom,
        width: shape.width * zoom,
        height: shape.height * zoom,
        padding: TEXT_PADDING * zoom,
        margin: 0,
        boxSizing: "border-box",
        border: "1px dashed var(--brand)",
        borderRadius: shape.type === "callout" ? 8 * zoom : 0,
        background: shape.type === "callout" ? shape.colour : "rgba(255, 255, 255, 0.6)",
        color: colour,
        font: `500 ${px}px ${UI_FONT}`,
        lineHeight: `${px * LINE_HEIGHT}px`,
        resize: "none",
        overflow: "hidden",
        outline: "none",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    />
  );
}
