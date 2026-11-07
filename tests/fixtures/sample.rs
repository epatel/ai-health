//! A small module exercising modern Rust syntax.

use std::collections::HashMap;
use std::fmt;

/// A user profile.
#[derive(Debug, Clone, PartialEq)]
pub struct Profile {
    pub id: u64,
    pub name: String,
    age: Option<u32>,
}

#[derive(Debug)]
pub enum LoadError {
    NotFound,
    Network { code: u16 },
    Parse(String),
}

pub trait Store {
    fn load(&self, id: u64) -> Result<Profile, LoadError>;

    fn exists(&self, id: u64) -> bool {
        self.load(id).is_ok()
    }
}

impl Profile {
    pub fn new(id: u64, name: impl Into<String>) -> Self {
        Self { id, name: name.into(), age: None }
    }

    pub fn is_adult(&self) -> bool {
        let Some(age) = self.age else { return false };
        age >= 18
    }

    pub fn greeting(&self, other: &Profile) -> String {
        if let Some(age) = other.age && age > 65 {
            format!("Hello, {}", other.name)
        } else if other.name.is_empty() {
            "Hi".to_string()
        } else {
            let result = if self.age.is_none() { "Hey" } else { "Yo" };
            result.into()
        }
    }
}

impl fmt::Display for Profile {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.name, self.id)
    }
}

pub struct Cache<S: Store> {
    store: S,
    items: HashMap<u64, Profile>,
}

impl<S: Store> Cache<S> {
    pub async fn load(&mut self, id: u64, retries: usize) -> Result<&Profile, LoadError> {
        'outer: for attempt in 0..retries {
            match self.store.load(id) {
                Ok(p) => {
                    self.items.insert(id, p);
                    break 'outer;
                }
                Err(LoadError::NotFound) => return Err(LoadError::NotFound),
                Err(LoadError::Network { code }) if code >= 500 && attempt < retries - 1 => {
                    continue;
                }
                Err(e) => return Err(e),
            }
        }
        self.items.get(&id).ok_or(LoadError::NotFound)
    }

    fn classify(value: i64) -> &'static str {
        match value {
            i64::MIN..=-1 => "negative",
            0 => "zero",
            1..=9 if value % 2 == 0 => "small even",
            _ => "other",
        }
    }

    pub fn sum(numbers: &[i64]) -> i64 {
        fn add(a: i64, b: i64) -> i64 {
            a + b
        }
        let mut total = 0;
        for n in numbers.iter().filter(|n| **n > 0) {
            if *n > 100 || *n == 42 {
                total = add(total, n * 2);
            } else if *n > 10 {
                total = add(total, *n);
            } else {
                total += 1;
            }
        }
        while total > 1000 {
            total -= 1;
        }
        loop {
            if total >= 0 {
                break;
            }
            total += 1;
        }
        numbers.iter().map(|x| x * 2).fold(total, |acc, x| acc + x)
    }
}

pub fn parse(input: &str) -> Result<u64, LoadError> {
    let trimmed = input.trim();
    let value: u64 = trimmed
        .parse()
        .map_err(|_| LoadError::Parse(trimmed.to_owned()))?;
    Ok(value)
}

macro_rules! square {
    ($x:expr) => {
        $x * $x
    };
}

const LIMIT: u64 = square!(4);

unsafe fn raw_read(ptr: *const u8) -> u8 {
    unsafe { *ptr }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_numbers() {
        assert_eq!(parse(" 42 ").ok(), Some(42));
    }

    #[test]
    fn classifies() {
        for v in [-5, 0, 4, 99] {
            assert!(!Cache::<Dummy>::classify(v).is_empty());
        }
    }

    struct Dummy;

    impl Store for Dummy {
        fn load(&self, _id: u64) -> Result<Profile, LoadError> {
            Err(LoadError::NotFound)
        }
    }
}
