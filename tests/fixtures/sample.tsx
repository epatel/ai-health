import { useState, type ReactNode } from "react";

type Props = { title: string; items: string[]; footer?: ReactNode };

export function ItemList({ title, items, footer }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <section className="list">
      <header>
        <h2 onClick={() => setOpen(!open)}>{title}</h2>
      </header>
      <hr />
      {open && (
        <ul>
          {items.map((item) => (
            <li key={item}>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
      {footer ?? <p>No footer</p>}
    </section>
  );
}

export const Badge = ({ label }: { label: string }) => <span className="badge">{label}</span>;
