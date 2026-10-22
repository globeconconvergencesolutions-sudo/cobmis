"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  text: string;
  onCite?: (tag: string) => void;
  activeTag?: string | null;
};

/** Split [S1]-style markers into clickable chips around markdown blocks. */
export function AnswerBody({ text, onCite, activeTag }: Props) {
  const parts = text.split(/(\[[Ss]\d+\])/g);
  return (
    <div className="prose-answer">
      {parts.map((part, i) => {
        const m = part.match(/^\[([Ss]\d+)\]$/);
        if (m) {
          const norm = `S${m[1].slice(1)}`;
          return (
            <button
              key={i}
              type="button"
              className={`cite-chip ${activeTag === norm ? "active" : ""}`}
              onClick={() => onCite?.(norm)}
            >
              {norm}
            </button>
          );
        }
        return (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>
            {part}
          </ReactMarkdown>
        );
      })}
    </div>
  );
}
