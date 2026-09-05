import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MarkdownContent, MarkdownExcerpt } from './Markdown';

describe('Markdown', () => {
  it('renders readable GFM without executing raw HTML or loading remote images', () => {
    const { container } = render(
      <MarkdownContent className="document">
        {`# Release

**Ready** with \`inline code\`.

- [x] Reviewed
- [ ] Published

| Gate | State |
| --- | --- |
| QA | Green |

> Ship carefully.

[Docs](https://example.com) [Section](#release) [Unsafe](javascript:alert(1))

![Tracking pixel](https://tracker.example/pixel.png)

<script>window.compromised = true</script>`}
      </MarkdownContent>,
    );

    expect(screen.getByRole('heading', { name: 'Release', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('Ready')).toHaveProperty('tagName', 'STRONG');
    expect(screen.getByText('inline code')).toHaveProperty('tagName', 'CODE');
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(
      screen.getAllByRole('checkbox').every((checkbox) => checkbox.hasAttribute('disabled')),
    ).toBe(true);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute(
      'rel',
      'noopener noreferrer',
    );
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'Section' })).not.toHaveAttribute('target');
    expect(screen.getByText('Unsafe').closest('a')).toBeNull();
    expect(screen.getByRole('img', { name: 'Tracking pixel' })).toHaveTextContent('Tracking pixel');
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });

  it('creates a compact, non-interactive excerpt that is valid inside a button', () => {
    const { container } = render(
      <button type="button">
        <MarkdownExcerpt className="summary">
          {`## Decision

Use **one path** and [read the rationale](https://example.com).

1. First
2. Second

---

| A | B |
| - | - |
| C | D |

\`code\`\\
next line

![Diagram](https://example.com/diagram.png)`}
        </MarkdownExcerpt>
      </button>,
    );

    const button = screen.getByRole('button');
    expect(within(button).getByText('Decision')).toHaveProperty('tagName', 'STRONG');
    expect(within(button).getByText('one path')).toHaveProperty('tagName', 'STRONG');
    expect(within(button).getByText('read the rationale')).toHaveClass('markdown-link-preview');
    expect(within(button).getByText('Diagram')).toBeInTheDocument();
    expect(button.querySelector('a')).toBeNull();
    expect(button.querySelector('p, h1, h2, h3, h4, h5, h6, ul, ol, li, table, img')).toBeNull();
    expect(container.querySelector('.summary')).toBeInTheDocument();
  });

  it('flattens every block variant and rejects malformed excerpt URLs', () => {
    const { container } = render(
      <MarkdownExcerpt>
        {`# One
### Three
#### Four
##### Five
###### Six

> Quoted

- Bullet
- [x] Checked

\`\`\`
code block
\`\`\`

[Malformed](https://%)`}
      </MarkdownExcerpt>,
    );

    for (const heading of ['One', 'Three', 'Four', 'Five', 'Six']) {
      expect(screen.getByText(heading)).toHaveProperty('tagName', 'STRONG');
    }
    expect(screen.getByText('Quoted')).toBeInTheDocument();
    expect(screen.getByText('Bullet')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.getByText('code block')).toHaveProperty('tagName', 'CODE');
    expect(screen.getByText('Malformed').closest('a')).toBeNull();
    expect(container.querySelector('blockquote, ul, pre, input')).toBeNull();
  });

  it('renders nothing for blank content', () => {
    const { container } = render(
      <>
        <MarkdownContent>{'   '}</MarkdownContent>
        <MarkdownExcerpt>{'\n'}</MarkdownExcerpt>
      </>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
