/** Web addresses in text Archangel or the owner wrote (a PR, a report, a run), as real links: click, open in a new
 * tab, copy. */

// Up to whitespace or a closing bracket or quote; punctuation ending a sentence isn't part of it.
const URL = /https?:\/\/[^\s<>"'()[\]]+[^\s<>"'()[\].,;:!?]/g;

export function Linked({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let at = 0;
  for (const found of text.matchAll(URL)) {
    parts.push(text.slice(at, found.index));
    parts.push(
      // A step's row opens its screen when clicked; the link shouldn't.
      <a
        key={found.index}
        href={found[0]}
        target="_blank"
        rel="noreferrer"
        className="linked"
        onClick={(e) => e.stopPropagation()}
      >
        {found[0]}
      </a>,
    );
    at = found.index + found[0].length;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}
