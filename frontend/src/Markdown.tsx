import type { ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

type MarkdownProps = {
  children: string;
  className?: string;
};

function classes(...values: Array<string | undefined>) {
  return values.filter(Boolean).join(' ');
}

function Inline({ children }: { children?: ReactNode }) {
  return <span>{children}</span>;
}

const fullComponents: Components = {
  a: ({ node: _node, href, children, ...props }) =>
    href ? (
      <a
        {...props}
        href={href}
        target={href.startsWith('#') ? undefined : '_blank'}
        rel="noopener noreferrer"
      >
        {children}
      </a>
    ) : (
      <span className="markdown-invalid-link">{children}</span>
    ),
  img: ({ node: _node, alt = '' }) => (
    <span className="markdown-image-placeholder" role="img" aria-label={alt || undefined}>
      🖼 {alt}
    </span>
  ),
  input: ({ node: _node, ...props }) => <input {...props} disabled />,
};

function safeUrl(value: string) {
  const url = value.trim();
  if (!url) return '';
  if (url.startsWith('#')) return url;
  try {
    const parsed = new URL(url, 'https://dduo.invalid');
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? value : '';
  } catch {
    return '';
  }
}

const excerptComponents: Components = {
  p: ({ node: _node, children }) => <Inline>{children}</Inline>,
  h1: ({ node: _node, children }) => <strong>{children}</strong>,
  h2: ({ node: _node, children }) => <strong>{children}</strong>,
  h3: ({ node: _node, children }) => <strong>{children}</strong>,
  h4: ({ node: _node, children }) => <strong>{children}</strong>,
  h5: ({ node: _node, children }) => <strong>{children}</strong>,
  h6: ({ node: _node, children }) => <strong>{children}</strong>,
  blockquote: ({ node: _node, children }) => <Inline>{children}</Inline>,
  ul: ({ node: _node, children }) => <Inline>{children}</Inline>,
  ol: ({ node: _node, children }) => <Inline>{children}</Inline>,
  li: ({ node: _node, children }) => <span className="markdown-list-item">{children}</span>,
  pre: ({ node: _node, children }) => <Inline>{children}</Inline>,
  table: ({ node: _node, children }) => <Inline>{children}</Inline>,
  thead: ({ node: _node, children }) => <Inline>{children}</Inline>,
  tbody: ({ node: _node, children }) => <Inline>{children}</Inline>,
  tr: ({ node: _node, children }) => <Inline>{children}</Inline>,
  th: ({ node: _node, children }) => <span className="markdown-table-cell">{children}</span>,
  td: ({ node: _node, children }) => <span className="markdown-table-cell">{children}</span>,
  a: ({ node: _node, children }) => <span className="markdown-link-preview">{children}</span>,
  img: ({ node: _node, alt = '' }) => <Inline>{alt}</Inline>,
  input: () => null,
  hr: () => <span aria-hidden="true"> · </span>,
  br: () => <> </>,
};

export function MarkdownContent({ children, className }: MarkdownProps) {
  if (!children.trim()) return null;
  return (
    <div className={classes('markdown-content', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={fullComponents} urlTransform={safeUrl}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

export function MarkdownExcerpt({ children, className }: MarkdownProps) {
  if (!children.trim()) return null;
  return (
    <span className={classes('markdown-excerpt', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={excerptComponents}
        urlTransform={safeUrl}
      >
        {children}
      </ReactMarkdown>
    </span>
  );
}
