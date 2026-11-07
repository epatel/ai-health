// A module exercising modern JavaScript syntax.
import { readFile } from "node:fs/promises";

/** A cache of profiles. */
export class ProfileCache extends EventTarget {
  #items = new Map();
  hits = 0;
  onHit = (id) => {
    this.hits++;
  };

  constructor(store, { logger = console, limit } = {}) {
    super();
    this.store = store;
    this.logger = logger;
  }

  get size() {
    return this.#items.size;
  }

  async load(id, retries = 3, ...rest) {
    outer: for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const profile = await this.store.load(id);
        if (!profile) {
          return null;
        }
        this.#items.set(id, profile);
        break outer;
      } catch (err) {
        if (err instanceof TypeError && attempt < retries - 1) {
          continue;
        }
        throw err;
      }
    }
    return this.#items.get(id) ?? null;
  }

  static classify(value) {
    switch (true) {
      case value < 0:
        return "negative";
      case value === 0:
        return "zero";
      default:
        return value % 2 === 0 ? "even" : "odd";
    }
  }
}

export function sum(numbers) {
  let total = 0;
  for (const n of numbers) {
    if (n > 100 || n === 42) {
      total += n * 2;
    } else if (n > 10) {
      total += n;
    } else {
      total += 1;
    }
  }
  return numbers.map((x) => x * 2).reduce((acc, x) => acc + x, total);
}

export const parse = async function (path) {
  const text = await readFile(path, "utf8");
  return JSON.parse(text);
};

module.exports = {
  format(profile) {
    return `${profile.name} (${profile.id})`;
  },
};

export function Card({ title, children }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      {children}
    </div>
  );
}
