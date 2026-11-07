// A module exercising modern TypeScript syntax.
import { readFile } from "node:fs/promises";
import type { Logger } from "./logger";

/** A user profile. */
export interface Profile {
  readonly id: string;
  name: string;
  age?: number;
}

export type LoadResult = { ok: true; profile: Profile } | { ok: false; error: LoadError };

export enum LoadError {
  NotFound = "not_found",
  Network = "network",
}

export abstract class Store {
  abstract load(id: string): Promise<Profile | undefined>;

  async exists(id: string): Promise<boolean> {
    return (await this.load(id)) !== undefined;
  }
}

@sealed
export class ProfileCache<S extends Store> {
  #items = new Map<string, Profile>();
  private hits = 0;

  constructor(
    private readonly store: S,
    private readonly logger?: Logger,
  ) {}

  get size(): number {
    return this.#items.size;
  }

  set limit(value: number) {
    this.logger?.info(`limit ${value}`);
  }

  async load(id: string, retries = 3): Promise<LoadResult> {
    outer: for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const profile = await this.store.load(id);
        if (!profile) {
          return { ok: false, error: LoadError.NotFound };
        }
        this.#items.set(id, profile);
        this.hits++;
        break outer;
      } catch (err) {
        if (err instanceof TypeError && attempt < retries - 1) {
          continue;
        }
        return { ok: false, error: LoadError.Network };
      } finally {
        this.logger?.debug("attempt done");
      }
    }
    const cached = this.#items.get(id);
    return cached ? { ok: true, profile: cached } : { ok: false, error: LoadError.NotFound };
  }

  static classify(value: number): string {
    switch (true) {
      case value < 0:
        return "negative";
      case value === 0:
        return "zero";
      case value < 10 && value % 2 === 0:
        return "small even";
      default:
        return "other";
    }
  }

  sum(numbers: readonly number[]): number {
    function add(a: number, b: number): number {
      return a + b;
    }
    let total = 0;
    for (const n of numbers.filter((n) => n > 0)) {
      if (n > 100 || n === 42) {
        total = add(total, n * 2);
      } else if (n > 10) {
        total = add(total, n);
      } else {
        total += 1;
      }
    }
    while (total > 1000) total--;
    do {
      total++;
    } while (total < 0);
    return numbers.map((x) => x * 2).reduce((acc, x) => acc + x, total);
  }
}

export function greeting(profile: Profile, other?: Profile): string {
  const name = other?.name ?? profile.name;
  if (other?.age !== undefined && other.age > 65) {
    return `Hello, ${name}`;
  }
  return profile.age === undefined ? "Hi" : "Hey";
}

export const parse = async (path: string): Promise<Profile[]> => {
  const text = await readFile(path, "utf8");
  return JSON.parse(text) as Profile[];
};

const helpers = {
  double(x: number) {
    return x * 2;
  },
  triple: (x: number) => x * 3,
};

function sealed(constructor: Function) {
  Object.seal(constructor);
}

export function* ids(start: number): Generator<string> {
  for (let i = start; ; i++) yield `id-${i}`;
}

declare function external(x: number): void;

describe("ProfileCache", () => {
  it("classifies numbers", () => {
    expect(ProfileCache.classify(-1)).toBe("negative");
  });
});
